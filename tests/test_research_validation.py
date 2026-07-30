from __future__ import annotations

from collections.abc import Iterator
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import polars as pl
import pytest

from market_structure_lab.core.identity import canonical_json, hash_json
from market_structure_lab.data.aggregate_bars import source_rows_sha256
from market_structure_lab.data.aggregate_publication import (
    VerifiedAggregateSeries,
    publish_aggregate_bars,
    read_verified_aggregate_series,
)
from market_structure_lab.data.canonical import CANONICAL_SCHEMA
from market_structure_lab.data.export import SnapshotIdentity, export_partitioned_snapshot
from market_structure_lab.research.candidates import detect_candidate_signals
from market_structure_lab.research.controls import (
    ControlOpportunity,
    LegalBoundaryInterval,
    SelectorInput,
)
from market_structure_lab.research.costs import (
    CostEventCoverage,
    CostEvidence,
    CostPolicy,
    CostRate,
    CostSide,
    freeze_cost_event_coverage_publication,
)
from market_structure_lab.research.models import (
    EXPECTED_FAMILIES,
    VALIDATION_SLOT_ROSTER,
    OutcomeComponent,
    OutcomePolicy,
    ScientificDecision,
    ValidationProgrammeConfig,
    ValidationSlot,
    ValidationSlotKind,
    ValidationWorkBudget,
    ValidationWorkDemand,
    candidate_definition_for_slot,
    evaluation_id_for_slot,
)
from market_structure_lab.research.receipts import (
    verify_evaluation_receipt,
    verify_programme_receipt,
)
from market_structure_lab.research.robustness import (
    AdjacentLookbackPerturbation,
    AdjacentLookbackPerturbationSet,
    OlsExposureEvent,
    RobustnessLowerBounds,
)
from market_structure_lab.research.splits import EventInterval, SymbolCoverage
from market_structure_lab.research.statistics import CostOpportunity, WeeklyVectorObservation
from market_structure_lab.research.validation import (
    CandidatePrimitiveCase,
    CostApplicationCase,
    DevelopmentOutcomeCase,
    ProgrammePreflightMetadata,
    ValidationProgrammeSources,
    freeze_development_evidence,
    run_validation_programme,
)

TASK14_CLOSEOUT = "5db2705a1cba37ee10d5eb3bd1fa470a5b628e3d"
TASK14_EVIDENCE = "3482882c864f1471ec1dd682631544d0c404c542"
TASK15_PLAN = "807ac28ac5616fb837c1ccea1e2bc47572ae3984"
TASK15_EVIDENCE = "afce8e889aa645cd9e48e90631698b87866f287f"
IMPLEMENTATION_CHECKPOINT = "e655152ab729b3e1e530f1bc044febca3509a6fd"
IMPLEMENTATION_EVIDENCE = "6da307a0d4756c61ddcabdf01f00d828afb55d7e"
IMPLEMENTATION_DOCUMENT = "d3520669352f0d85a27569edeefcfe84ff785e1f928ae0cc41edf91915c16111"
CODE_COMMIT = "45df56cb27bb844e257ef82c8c8012997c0f27e9"
LOCKFILE = "a16b349b97b4c265129df59092f69981d9150a131772b8ed9f9ebda28dcaf44b"
START = datetime(2024, 1, 1, tzinfo=UTC)
SYMBOL = "SOLUSDT"


def _config(series: VerifiedAggregateSeries, **changes: object) -> ValidationProgrammeConfig:
    values: dict[str, object] = {
        "task14_closeout_commit": TASK14_CLOSEOUT,
        "task14_evidence_commit": TASK14_EVIDENCE,
        "task15_plan_commit": TASK15_PLAN,
        "task15_evidence_commit": TASK15_EVIDENCE,
        "implementation_plan_checkpoint": IMPLEMENTATION_CHECKPOINT,
        "implementation_plan_evidence_commit": IMPLEMENTATION_EVIDENCE,
        "implementation_plan_document_sha256": IMPLEMENTATION_DOCUMENT,
        "code_commit": CODE_COMMIT,
        "lockfile_sha256": LOCKFILE,
        "dataset_sha256": series.publication_sha256,
        "cost_policy_sha256": "4" * 64,
        "control_policy_sha256": "5" * 64,
        "split_sha256": "6" * 64,
        "profile_config_sha256": "7" * 64,
        "source_price_precision_sha256": "8" * 64,
        "families": EXPECTED_FAMILIES,
        "roster": VALIDATION_SLOT_ROSTER,
        "work_budget": ValidationWorkBudget(max_artifacts=20_000),
    }
    values.update(changes)
    return ValidationProgrammeConfig(**values)  # type: ignore[arg-type]


def _slot(family: str, role: str) -> ValidationSlot:
    for slot in VALIDATION_SLOT_ROSTER:
        if (
            slot.family == family
            and slot.role == role
            and slot.direction == "long"
            and slot.kind is ValidationSlotKind.CORE
        ):
            return slot
    raise AssertionError(f"slot not found: {family} {role}")


def _hourly() -> list[tuple[float, float, float, float, float]]:
    bars = [(100.0, 105.0, 95.0, 100.0, 10.0) for _ in range(22)] + [
        (100.0, 101.0, 99.0, 100.0, 10.0) for _ in range(198)
    ]
    bars[30] = (100.0, 125.0, 99.0, 124.0, 50.0)  # A/E/G long breakout + volume confirmation
    bars[70] = (100.0, 101.0, 95.0, 100.0, 10.0)  # D failed break/reclaim setup
    bars[71] = (100.0, 102.0, 99.0, 101.0, 10.0)
    bars[80] = (100.0, 101.0, 99.0, 100.0, 10.0)  # outcome entry open
    bars[104] = (112.0, 113.0, 111.0, 112.0, 10.0)  # outcome exit open
    return bars


def _minute_rows(hourly: list[tuple[float, float, float, float, float]]) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for hour, (open_, high, low, close, volume) in enumerate(hourly):
        for minute in range(60):
            timestamp = START + timedelta(hours=hour, minutes=minute)
            price = open_ + (close - open_) * minute / 59 if minute else open_
            rows.append(
                {
                    "timestamp": timestamp,
                    "symbol": SYMBOL,
                    "timeframe": "1m",
                    "open": price,
                    "high": max(price, high if minute == 30 else price),
                    "low": min(price, low if minute == 30 else price),
                    "close": close if minute == 59 else price,
                    "volume": volume,
                    "segment_id": 0,
                }
            )
    return rows


def _series(tmp_path: Path) -> tuple[VerifiedAggregateSeries, list[dict[str, object]]]:
    rows = _minute_rows(_hourly())
    schema = pl.Schema(CANONICAL_SCHEMA)
    schema["segment_id"] = pl.UInt64
    frame = pl.DataFrame(rows, schema=schema)
    source = frame.drop("segment_id")
    snapshot_root = tmp_path / "snapshot"
    identity = SnapshotIdentity(
        dataset_version="DS-VALIDATION-TEST",
        dump_sha256="1" * 64,
        recovery_sha256="2" * 64,
        mapping_version="canonical-validation-test-v1",
        config_version="canonical-validation-test-v1",
        code_commit="3" * 40,
    )
    snapshot = export_partitioned_snapshot(
        [source], output_root=snapshot_root, identity=identity, expected_row_count=len(rows)
    )
    snapshot_directory = snapshot_root / f"dataset_version={identity.dataset_version}"
    manifest = publish_aggregate_bars(
        [frame],
        output_root=tmp_path / "aggregate",
        parent_snapshot_directory=snapshot_directory,
        parent_snapshot_manifest=snapshot,
        symbol=SYMBOL,
        segment_id=0,
        target_timeframe="1h",
        expected_source_sha256=source_rows_sha256(rows),
        config_version="validation-test-v1",
        demand=ValidationWorkDemand(
            source_rows=len(rows),
            source_bytes=sum(
                len(canonical_json("canonical-source-minute", {"schema_version": 1, **row}))
                for row in rows
            ),
            aggregate_bars=len(rows) // 60,
            artifacts=1,
            artifact_bytes=16 * 1024 * 1024,
        ),
        budget=ValidationWorkBudget(max_artifacts=20_000),
    )
    verified = read_verified_aggregate_series(
        tmp_path / "aggregate/symbol=SOLUSDT/timeframe=1h/segment=0",
        expected_publication_sha256=manifest.publication_sha256,
        parent_snapshot_directory=snapshot_directory,
        expected_parent_snapshot_sha256=snapshot.snapshot_sha256,
        budget=ValidationWorkBudget(),
    )
    return verified, rows


def _path(rows: list[dict[str, object]], signal_time: datetime) -> list[dict[str, object]]:
    end = signal_time + timedelta(hours=24)
    return [row for row in rows if signal_time <= row["timestamp"] < end]


def _coverage() -> tuple[SymbolCoverage, ...]:
    start = datetime(2023, 1, 1, tzinfo=UTC)
    end = start + timedelta(days=732)
    symbols = ("SOLUSDT", "BNBUSDT", "XRPUSDT", "ADAUSDT", "DOGEUSDT", "LTCUSDT")
    return tuple(
        SymbolCoverage(symbol, start, end, False, True, hash_json("coverage", {"symbol": symbol}))
        for symbol in symbols
    )


def _metadata() -> ProgrammePreflightMetadata:
    return ProgrammePreflightMetadata(
        code_commit=CODE_COMMIT,
        lockfile_sha256=LOCKFILE,
        repository_clean=True,
        cost_policy_sha256="4" * 64,
        work_demand=ValidationWorkDemand(
            symbols=6,
            ranges=6,
            candidates=64,
            trials=1_104,
            events=10,
            outcomes=10,
            outer_folds=4,
            inner_folds=3,
            evaluations=1_104,
            bootstrap_draws=4_096,
            controls=384,
            placebos=192,
            perturbations=184,
            artifacts=2_209,
        ),
    )


def _candidate_cases(series: VerifiedAggregateSeries) -> dict[str, CandidatePrimitiveCase]:
    a_def = candidate_definition_for_slot(_slot("A", "donchian_breakout"), series)
    a_signals = detect_candidate_signals(a_def, series)
    assert a_signals
    cases = {"A": CandidatePrimitiveCase("A", a_def.slot, a_def, a_signals)}
    for family, role in (("G", "candidate_primary"), ("D", "candidate_primary")):
        definition = candidate_definition_for_slot(_slot(family, role), series)
        signals = detect_candidate_signals(definition, series)
        assert signals, family
        cases[family] = CandidatePrimitiveCase(family, definition.slot, definition, signals)
    e_def = candidate_definition_for_slot(
        _slot("E", "volume_filtered_primary"), series, parent_a_candidate=a_def
    )
    e_signals = detect_candidate_signals(e_def, series, a_opportunities=(a_signals[0],))
    assert e_signals
    cases["E"] = CandidatePrimitiveCase("E", e_def.slot, e_def, e_signals)
    b_slot = _slot("B", "combined_primary")
    cases["B"] = CandidatePrimitiveCase("B", b_slot)
    return cases


def _cost_policy(
    series: VerifiedAggregateSeries, event_id: str, event_time: datetime
) -> CostPolicy:
    coverage = CostEventCoverage(
        event_id,
        "binance",
        SYMBOL,
        "1h",
        event_time - timedelta(days=1),
        event_time + timedelta(days=1),
        "9" * 64,
    )
    publication = freeze_cost_event_coverage_publication(
        [coverage],
        source_publication_sha256=series.publication_sha256,
        budget=ValidationWorkBudget(),
        demand=ValidationWorkDemand(events=1),
    )
    rate = CostRate(0.0001, "proportion_of_notional", "unit-test", "a" * 64)
    evidence = CostEvidence(
        "CE-1",
        SYMBOL,
        "1h",
        series.publication_sha256,
        rate,
        rate,
        rate,
        rate,
        rate,
        rate,
        1.0,
        event_coverage_publication=publication,
    )
    policy = CostPolicy("CP-1", "4" * 64, evidence)
    assert policy.sha256
    return policy


def _control_rows(
    config: ValidationProgrammeConfig,
    publication: str,
    candidate_cases: dict[str, CandidatePrimitiveCase],
) -> tuple[
    tuple[ControlOpportunity, ...],
    tuple[ControlOpportunity, ...],
    tuple[SelectorInput, ...],
    tuple[LegalBoundaryInterval, ...],
]:
    week = datetime(2024, 1, 1, tzinfo=UTC)
    start = START + timedelta(hours=30)
    kwargs = dict(
        symbol=SYMBOL,
        timeframe="1h",
        fold_id="outer-1/inner-1",
        utc_week_start=week,
        direction=1,
        horizon_hours=24,
        segment_id=0,
        feature_time=start - timedelta(hours=24),
        signal_time=start,
        legal_entry_time=start,
        label_start=start,
        label_end=start + timedelta(hours=24),
        programme_id=config.programme_id,
        publication_sha256=publication,
        component="development",
        block_id="block-1",
        gross_return=0.10,
        cost_return=0.001,
        prior_completed_close_return=0.01,
    )
    roles_by_family = {
        "A": ("unconditional", "persistence"),
        "B": ("price_baseline", "structure_only"),
        "G": ("atr_only", "donchian_only"),
        "E": ("price_only", "rate_matched_placebo"),
        "D": ("failed_donchian", "pseudo_level"),
    }
    candidates = tuple(
        ControlOpportunity(
            f"candidate-{family}",
            f"EV-{family}-candidate",
            (candidate_cases[family].definition or candidate_cases["A"].definition).candidate_id,  # type: ignore[union-attr]
            family,
            "candidate",
            **kwargs,
        )
        for family in EXPECTED_FAMILIES
    )
    pool = tuple(
        ControlOpportunity(
            f"control-{family}-{role}",
            f"EV-{family}-{role}",
            (candidate_cases[family].definition or candidate_cases["A"].definition).candidate_id,  # type: ignore[union-attr]
            family,
            role,
            **kwargs,
        )
        for family in EXPECTED_FAMILIES
        for role in roles_by_family[family]
    )
    selectors = tuple(
        SelectorInput(row.row_identity, publication, row.feature_time)
        for row in (*candidates, *pool)
    )
    boundaries = (
        LegalBoundaryInterval(
            config.programme_id,
            publication,
            SYMBOL,
            "1h",
            0,
            "outer-1/inner-1",
            "development",
            "block-1",
            START,
            START + timedelta(days=10),
        ),
    )
    return candidates, pool, selectors, boundaries


def _weekly(
    series: VerifiedAggregateSeries, effect: float
) -> dict[str, tuple[WeeklyVectorObservation, ...]]:
    out: dict[str, tuple[WeeklyVectorObservation, ...]] = {}
    assets = ("SOLUSDT", "BNBUSDT")
    for family in EXPECTED_FAMILIES:
        rows = []
        for week in range(16):
            value = (effect + week * 0.0001) if effect else (-0.0008 + week * 0.0001)
            for asset in assets:
                rows.append(
                    WeeklyVectorObservation(
                        f"{family}-{asset}-{week}",
                        asset,
                        START + timedelta(days=7 * week),
                        {"net_return": value},
                        series.publication_sha256,
                    )
                )
        out[family] = tuple(rows)
    return out


def _robustness(
    candidate_cases: dict[str, CandidatePrimitiveCase], positive: bool
) -> dict[str, RobustnessLowerBounds]:
    value = 0.08 if positive else -0.01
    out = {}
    for family in EXPECTED_FAMILIES:
        definition = candidate_cases[family].definition or candidate_cases["A"].definition
        assert definition is not None
        parameter = {
            "A": "donchian",
            "B": "profile",
            "G": "donchian",
            "E": "donchian",
            "D": "level",
        }[family]
        perturb = AdjacentLookbackPerturbationSet(
            definition.candidate_id,
            family,
            candidate_cases[family].slot.slot_id,
            {parameter: 24},
            (
                AdjacentLookbackPerturbation(parameter, 24, 23, "n_L-1", value),
                AdjacentLookbackPerturbation(parameter, 24, 25, "n_L+1", value),
            ),
        )
        out[family] = RobustnessLowerBounds(
            value, value, value, value, value, perturb, (value, value, value, value)
        )
    return out


def _exposure(positive: bool) -> dict[str, tuple[OlsExposureEvent, ...]]:
    out: dict[str, tuple[OlsExposureEvent, ...]] = {}
    for family in EXPECTED_FAMILIES:
        train = tuple(
            OlsExposureEvent(
                f"{family}-tr-{index}",
                asset,
                year,
                1.0 + 0.5 * market + residual,
                market,
                trend,
                vol_rank,
                turn_rank,
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
        validation = (
            OlsExposureEvent(
                f"{family}-va-1",
                "XRP",
                2026,
                4.50 if positive else -4.50,
                6.0,
                3.0,
                0.9,
                1.0,
            ),
            OlsExposureEvent(
                f"{family}-va-2",
                "ADA",
                2027,
                5.00 if positive else -5.00,
                7.0,
                3.5,
                1.0,
                1.1,
            ),
        )
        out[family] = (*train, *validation)
    return out


class ExplodingIterable:
    def __init__(self) -> None:
        self.iterated = False

    def __iter__(self) -> Iterator[object]:
        self.iterated = True
        raise AssertionError("must not iterate")


def _sources(
    tmp_path: Path, *, effect: float, positive_gates: bool = False
) -> tuple[ValidationProgrammeConfig, ValidationProgrammeSources]:
    series, rows = _series(tmp_path)
    cases = _candidate_cases(series)
    a_signal = cases["A"].signals[0]
    outcome_case = DevelopmentOutcomeCase(
        a_signal,
        _path(rows, a_signal.legal_entry),
        OutcomePolicy(
            24,
            OutcomeComponent.DEVELOPMENT,
            series.publication_sha256,
            series.parent_snapshot_manifest.snapshot_sha256,
        ),
    )
    policy = _cost_policy(series, "EV-1", a_signal.legal_entry)
    config = _config(series, cost_policy_sha256=policy.sha256)
    candidates, pool, selectors, boundaries = _control_rows(
        config, series.publication_sha256, cases
    )
    exposures = _exposure(positive_gates)
    development_evidence = freeze_development_evidence(
        config=config,
        aggregate_series=series,
        candidate_cases=cases,
        weekly_observations=_weekly(series, effect),
        robustness_lower_bounds=_robustness(cases, positive_gates),
        exposure_train_events={family: values[:8] for family, values in exposures.items()},
        exposure_validation_events={family: values[8:] for family, values in exposures.items()},
    )
    return config, ValidationProgrammeSources(
        metadata=replace(_metadata(), cost_policy_sha256=config.cost_policy_sha256),
        symbol_coverages=_coverage(),
        aggregate_series=series,
        candidate_cases=cases,
        outcome_cases=(outcome_case,),
        purge_training=(
            EventInterval(
                "train-1",
                SYMBOL,
                START,
                START + timedelta(hours=1),
                START + timedelta(hours=1),
                START + timedelta(hours=25),
            ),
        ),
        purge_test=(
            EventInterval(
                "test-1",
                SYMBOL,
                datetime(2023, 5, 4, tzinfo=UTC),
                datetime(2023, 5, 4, 1, tzinfo=UTC),
                datetime(2023, 5, 4, 1, tzinfo=UTC),
                datetime(2023, 5, 5, 1, tzinfo=UTC),
            ),
        ),
        legal_boundaries=boundaries,
        control_candidates=candidates,
        control_pool=pool,
        selector_inputs=selectors,
        inner_selector_scores={
            row.row_identity: (1.0 if row.row_identity == candidates[0].row_identity else 0.0)
            for row in selectors
        },
        outer_diagnostic_scores={
            row.row_identity: (1.0 if row.row_identity == pool[0].row_identity else 0.0)
            for row in selectors
        },
        cost_policy=policy,
        cost_cases=(
            CostApplicationCase(
                CostSide.LONG, 100.0, 112.0, "EV-1", a_signal.legal_entry, "binance", SYMBOL, "1h"
            ),
        ),
        cost_opportunities=(
            CostOpportunity("cost-1", 0.001, "development", series.publication_sha256),
            CostOpportunity("cost-2", 0.0011, "development", series.publication_sha256),
        ),
        development_evidence=development_evidence,
    )


def _vr_dirs(root: Path) -> tuple[Path, ...]:
    return tuple(sorted(path for path in root.iterdir() if path.name.startswith("VR-")))


def _tree(root: Path) -> dict[str, bytes]:
    return {
        str(path.relative_to(root)): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


@pytest.fixture(scope="module")
def base_sources(
    tmp_path_factory: pytest.TempPathFactory,
) -> tuple[ValidationProgrammeConfig, ValidationProgrammeSources]:
    return _sources(tmp_path_factory.mktemp("validation-primitives"), effect=0.0)


def _with_effect(
    config: ValidationProgrammeConfig,
    sources: ValidationProgrammeSources,
    *,
    effect: float,
    positive_gates: bool,
) -> ValidationProgrammeSources:
    exposures = _exposure(positive_gates)
    development_evidence = freeze_development_evidence(
        config=config,
        aggregate_series=sources.aggregate_series,
        candidate_cases=sources.candidate_cases,
        weekly_observations=_weekly(sources.aggregate_series, effect),
        robustness_lower_bounds=_robustness(dict(sources.candidate_cases), positive_gates),
        exposure_train_events={family: values[:8] for family, values in exposures.items()},
        exposure_validation_events={family: values[8:] for family, values in exposures.items()},
    )
    return replace(
        sources,
        development_evidence=development_evidence,
    )


def test_no_edge_publishes_verified_vp_and_1104_vrs(
    tmp_path: Path,
    base_sources: tuple[ValidationProgrammeConfig, ValidationProgrammeSources],
) -> None:
    config, sources = base_sources
    result = run_validation_programme(config, sources, tmp_path / "run")
    assert result.stage_order[:4] == (
        "aggregate_capability",
        "human_candidate",
        "A_candidate_definition",
        "A_signal",
    )
    assert result.family_decisions["A"] in {
        ScientificDecision.REJECTED,
        ScientificDecision.INCONCLUSIVE,
    }
    assert len(result.programme_receipt.ledger) == 1_104
    assert len(result.evaluation_receipts) == 1_104
    assert len(_vr_dirs(result.output_root)) == 1_104
    assert verify_programme_receipt(result.programme_receipt_path) == result.programme_receipt
    assert {item.receipt.evaluation_id: item.receipt for item in result.evaluation_receipts} == {
        receipt.evaluation_id: receipt
        for receipt in (
            verify_evaluation_receipt(path, config=config) for path in _vr_dirs(result.output_root)
        )
    }
    assert tuple(item.receipt.evaluation_id for item in result.evaluation_receipts) == tuple(
        evaluation_id_for_slot(config, slot) for slot in VALIDATION_SLOT_ROSTER
    )


def test_v1_characterization_reuses_one_family_statistic_across_slot_receipts(
    tmp_path: Path,
    base_sources: tuple[ValidationProgrammeConfig, ValidationProgrammeSources],
) -> None:
    """Lock the rejected V1 family-level computation shape without endorsing it for V2."""

    config, sources = base_sources
    result = run_validation_programme(config, sources, tmp_path / "v1-family-reuse")
    family_a = tuple(item for item in result.slot_evidence if item.family == "A")

    assert len(family_a) > 1
    assert len({item.primitive_evidence_sha256 for item in family_a}) == 1
    assert len({item.family_decision_sha256 for item in family_a}) == 1
    assert len(
        {
            publication.receipt.evaluation_id
            for publication in result.evaluation_receipts
            if publication.receipt.slot["family"] == "A"
        }
    ) == len(family_a)


def test_known_effect_uses_raw_primitives_and_demotes_diagnostics(
    tmp_path: Path,
    base_sources: tuple[ValidationProgrammeConfig, ValidationProgrammeSources],
) -> None:
    config, base = base_sources
    sources = _with_effect(config, base, effect=0.10, positive_gates=True)
    result = run_validation_programme(config, sources, tmp_path / "known")
    assert result.family_decisions["G"] is ScientificDecision.SUPPORTED_DEVELOPMENT
    assert result.selection_policy == "inner_folds_only"
    assert result.inner_selected_row_identity == "candidate-A"
    assert result.outer_diagnostic_best_row_identity != result.inner_selected_row_identity
    assert all(
        item.selected_by == "inner"
        for item in result.slot_evidence
        if item.family == "A"
        and item.slot_id
        in {slot.slot_id for slot in VALIDATION_SLOT_ROSTER if slot.family == "A" and slot.primary}
    )
    assert all(
        item.selected_by == "diagnostic_only"
        and item.terminal_state.decision is not ScientificDecision.SUPPORTED_DEVELOPMENT
        for item in result.slot_evidence
        if not next(slot for slot in VALIDATION_SLOT_ROSTER if slot.slot_id == item.slot_id).primary
    )


def test_preflight_failure_publishes_failed_receipts_before_deferred_iteration(
    tmp_path: Path,
    base_sources: tuple[ValidationProgrammeConfig, ValidationProgrammeSources],
) -> None:
    config, sources = base_sources
    forbidden = ExplodingIterable()
    bad = replace(
        sources,
        metadata=replace(sources.metadata, repository_clean=False),
        deferred_source_rows=forbidden,
    )
    with pytest.raises(ValueError, match="dirty"):
        run_validation_programme(config, bad, tmp_path / "bad")
    assert forbidden.iterated is False
    receipt = verify_programme_receipt(tmp_path / "bad" / config.programme_id)
    assert {row["terminal_state"]["execution_status"] for row in receipt.ledger} == {"failed"}
    assert len(_vr_dirs(tmp_path / "bad")) == 1_104


@pytest.mark.parametrize("mutation", ("btc", "conflict", "mapping", "few", "short"))
def test_typed_source_preflight_rejects_before_deferred_iteration(
    tmp_path: Path,
    base_sources: tuple[ValidationProgrammeConfig, ValidationProgrammeSources],
    mutation: str,
) -> None:
    config, sources = base_sources
    coverages = list(sources.symbol_coverages)
    if mutation == "btc":
        coverages[0] = replace(coverages[0], symbol="BTCUSDT")
    elif mutation == "conflict":
        coverages[0] = replace(coverages[0], source_conflict=True)
    elif mutation == "mapping":
        coverages[0] = replace(coverages[0], mapping_compatible=False)
    elif mutation == "few":
        coverages = coverages[:4]
    else:
        coverages = [
            replace(item, complete_end=item.complete_start + timedelta(days=729))
            for item in coverages
        ]
    forbidden = ExplodingIterable()
    invalid = replace(
        sources,
        symbol_coverages=tuple(coverages),
        deferred_source_rows=forbidden,
    )

    with pytest.raises(ValueError):
        run_validation_programme(config, invalid, tmp_path / mutation)

    assert forbidden.iterated is False
    assert len(_vr_dirs(tmp_path / mutation)) == 1_104


def test_clean_root_replay_is_byte_identical(
    tmp_path: Path,
    base_sources: tuple[ValidationProgrammeConfig, ValidationProgrammeSources],
) -> None:
    config, sources = base_sources
    first = run_validation_programme(config, sources, tmp_path / "first")
    second = run_validation_programme(config, sources, tmp_path / "second")

    assert first.programme_receipt.receipt_sha256 == second.programme_receipt.receipt_sha256
    assert _tree(first.output_root) == _tree(second.output_root)


def test_existing_output_rejects_without_overwrite(
    tmp_path: Path,
    base_sources: tuple[ValidationProgrammeConfig, ValidationProgrammeSources],
) -> None:
    config, sources = base_sources
    output = tmp_path / "existing"
    output.mkdir()
    marker = output / "marker.txt"
    marker.write_text("keep", encoding="utf-8")

    with pytest.raises(FileExistsError):
        run_validation_programme(config, sources, output)

    assert marker.read_text(encoding="utf-8") == "keep"


def test_whole_programme_publication_failure_leaves_no_partial_root(
    tmp_path: Path,
    base_sources: tuple[ValidationProgrammeConfig, ValidationProgrammeSources],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import market_structure_lab.research.validation as validation_module

    config, sources = base_sources
    invalid = replace(sources, metadata=replace(sources.metadata, repository_clean=False))

    def fail_programme_publication(*args: object, **kwargs: object) -> None:
        raise OSError("synthetic programme receipt failure")

    monkeypatch.setattr(
        validation_module,
        "publish_programme_receipt",
        fail_programme_publication,
    )
    output = tmp_path / "atomic-failure"
    with pytest.raises(OSError, match="synthetic programme receipt failure"):
        run_validation_programme(config, invalid, output)

    assert not output.exists()
    assert not any(path.name.startswith(".atomic-failure.staging") for path in tmp_path.iterdir())


def test_development_evidence_cannot_be_reconstructed_without_factory_seal(
    base_sources: tuple[ValidationProgrammeConfig, ValidationProgrammeSources],
) -> None:
    _config, sources = base_sources
    with pytest.raises(TypeError, match="issued by its factory"):
        replace(sources.development_evidence)


def test_final_holdout_iterable_is_rejected_by_constructor(
    base_sources: tuple[ValidationProgrammeConfig, ValidationProgrammeSources],
) -> None:
    _config, sources = base_sources
    with pytest.raises(TypeError):
        ValidationProgrammeSources(
            **{
                **{name: getattr(sources, name) for name in sources.__dataclass_fields__},
                "final_holdout_rows": ExplodingIterable(),
            }
        )  # type: ignore[arg-type]


def test_public_export_is_lazy() -> None:
    import market_structure_lab.research as research

    assert "run_validation_programme" in research.__all__
    assert research.run_validation_programme is run_validation_programme
    assert research.freeze_development_evidence is freeze_development_evidence
    assert "SyntheticValidationScenario" not in research.__all__
