from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Context, Decimal
import hashlib
from pathlib import Path
from types import MappingProxyType, SimpleNamespace
from typing import Any, cast

import pytest

from market_structure_lab.data.validation_precision_authority_v2 import (
    PrecisionSourceKindV2,
    publish_validation_precision_authority_v2,
)
from market_structure_lab.data.validation_source_v2 import CanonicalMinuteRowV2
from market_structure_lab.research.models import (
    VALIDATION_SLOT_ROSTER,
    ValidationSlot,
    ValidationWorkBudget,
    ValidationWorkBudgetViolation,
    ValidationWorkDemand,
)
from market_structure_lab.research.outcomes import DevelopmentOutcomeRowV2
from market_structure_lab.research.validation_v2 import (
    ValidationV2SourceBundle,
    derive_verified_vs0001_candidate_inputs_v2,
    derive_verified_vs0001_control_inputs_v2,
    development_access_ledger_identity_v2,
    run_validation_programme_v2,
    verify_original_validation_slot_result_v2,
)
from market_structure_lab.research.validation_v2_costs import (
    publish_validation_cost_authority_v2,
)
from market_structure_lab.research.validation_v2_inference import RawPrimaryOpportunityRowV2
from market_structure_lab.research.validation_v2_models import (
    PHASE5_BOOTSTRAP_HOLM_POLICY_IDENTITY,
    PHASE5_VS0001_POPULATION_POLICY_IDENTITY,
    SourceCoverageIdentityV2,
    ValidationProgrammeConfigV2,
    ValidationRosterIdentityV2,
)
from market_structure_lab.research.validation_v2_receipts import (
    publish_validation_v2_receipts,
    verify_validation_programme_v2,
)

pytest_plugins = ("test_aggregate_publication_v2",)


def _vs0001() -> ValidationSlot:
    return next(slot for slot in VALIDATION_SLOT_ROSTER if slot.slot_id == "VS-0001")


def _stub_population_sources() -> Any:
    partition = SimpleNamespace(segment_id="segment-1")
    member = SimpleNamespace(
        symbol="BTCUSDT",
        target_timeframe="1h",
        interval_index=0,
        partitions=(partition,),
    )
    return SimpleNamespace(
        split=SimpleNamespace(development_symbols=("BTCUSDT",)),
        aggregate_publication=SimpleNamespace(members=(member,)),
    )


def _stub_population_signal(
    signal_id: str,
    legal_entry: datetime,
    *,
    symbol: str = "BTCUSDT",
) -> SimpleNamespace:
    return SimpleNamespace(
        signal_id=signal_id,
        symbol=symbol,
        timeframe="1h",
        segment_id="segment-1",
        source_series_sha256="a" * 64,
        legal_entry=legal_entry,
        direction=1,
    )


def _control_material_fixture(*, price_offset: Decimal, input_sha256: str) -> tuple[Any, Any]:
    from market_structure_lab.core.identity import hash_json

    start = datetime(2025, 1, 6, tzinfo=UTC)
    rows: list[SimpleNamespace] = []
    for index in range(100):
        close = Decimal("101") if index in {1, 4, 59} else Decimal("100")
        open_ = close + price_offset
        rows.append(
            SimpleNamespace(
                timestamp=start + timedelta(hours=index),
                symbol="BTCUSDT",
                target_timeframe="1h",
                interval_index=1,
                segment_id=0,
                open=open_,
                close=close,
                row_sha256=hash_json(
                    "test-control-material-row",
                    {"index": index, "open": str(open_), "close": str(close)},
                ),
            )
        )
    series = SimpleNamespace(
        rows=tuple(rows),
        series_identity=hash_json(
            "test-control-material-series",
            {"price_offset": str(price_offset)},
        ),
        key=SimpleNamespace(interval_index=1),
    )
    entry_index = 60
    candidate = RawPrimaryOpportunityRowV2(
        row_identity="VPI2-test-candidate",
        event_id="CS-test-candidate",
        candidate_id="HC-A-001",
        family="A",
        detector_role="moving_average_crossover",
        detector_parameters=(
            ("detector", "moving_average_crossover"),
            ("fast_hours", "24"),
            ("slow_hours", "72"),
        ),
        control_role="candidate",
        symbol="BTCUSDT",
        timeframe="1h",
        fold_id="outer-1",
        utc_week_start=start,
        direction=1,
        horizon_hours=24,
        segment_id=0,
        feature_time=rows[entry_index - 2].timestamp,
        signal_time=rows[entry_index - 1].timestamp,
        legal_entry_time=rows[entry_index].timestamp,
        label_start=rows[entry_index].timestamp,
        label_end=rows[entry_index + 24].timestamp,
        programme_id="VPV2-" + "a" * 64,
        publication_sha256="b" * 64,
        component="development",
        block_id=start.date().isoformat(),
        venue="binance_spot",
        entry_price=float(rows[entry_index].open),
        exit_price=float(rows[entry_index + 24].open),
        delayed_entry_price=float(rows[entry_index + 1].open),
        delayed_exit_price=float(rows[entry_index + 25].open),
        delayed_entry_time=rows[entry_index + 1].timestamp,
        delayed_exit_time=rows[entry_index + 25].timestamp,
        delay_evidence_sha256="c" * 64,
        prior_completed_close_return=0.01,
    )
    candidate_inputs = SimpleNamespace(rows=(candidate,), input_set_sha256=input_sha256)
    registration = SimpleNamespace(
        signals=(SimpleNamespace(signal_id=candidate.event_id),),
        aggregate_series=(series,),
        folds=SimpleNamespace(
            outer_folds=(
                SimpleNamespace(
                    fold_id=candidate.fold_id,
                    test=SimpleNamespace(start=start, end=start + timedelta(days=7)),
                ),
            )
        ),
    )
    return candidate_inputs, registration


def test_control_donor_selection_is_outcome_blind_and_non_overlapping() -> None:
    from market_structure_lab.research import validation_v2 as module

    first_inputs, first_registration = _control_material_fixture(
        price_offset=Decimal("0"),
        input_sha256="d" * 64,
    )
    second_inputs, second_registration = _control_material_fixture(
        price_offset=Decimal("7"),
        input_sha256="e" * 64,
    )

    first = module._derive_vs0001_control_input_material_v2(  # noqa: SLF001
        first_inputs,
        first_registration,
    )
    second = module._derive_vs0001_control_input_material_v2(  # noqa: SLF001
        second_inputs,
        second_registration,
    )

    assert first[2] == second[2] == ()
    assert tuple(row.legal_entry_time for rows in first[:2] for row in rows) == tuple(
        row.legal_entry_time for rows in second[:2] for row in rows
    )
    assert tuple(row.row_identity for rows in first[:2] for row in rows) != tuple(
        row.row_identity for rows in second[:2] for row in rows
    )
    candidate = first_inputs.rows[0]
    for rows in first[:2]:
        [control] = rows
        assert control.label_end <= candidate.label_start
        assert control.legal_entry_time != candidate.legal_entry_time


def test_control_donor_horizons_never_overlap_across_roles() -> None:
    from market_structure_lab.research import validation_v2 as module

    candidate_inputs, registration = _control_material_fixture(
        price_offset=Decimal("0"),
        input_sha256="d" * 64,
    )

    unconditional, persistence, _ = module._derive_vs0001_control_input_material_v2(  # noqa: SLF001
        candidate_inputs,
        registration,
    )

    [unconditional_row] = unconditional
    [persistence_row] = persistence
    assert (
        unconditional_row.label_end <= persistence_row.label_start
        or persistence_row.label_end <= unconditional_row.label_start
    )


def _stub_population_dependencies(
    monkeypatch: pytest.MonkeyPatch,
    module: Any,
) -> None:
    test_window = SimpleNamespace(
        start=datetime(2025, 1, 1, tzinfo=UTC),
        end=datetime(2027, 1, 1, tzinfo=UTC),
    )
    monkeypatch.setattr(
        module,
        "freeze_development_folds_v2",
        lambda **_kwargs: SimpleNamespace(outer_folds=(SimpleNamespace(test=test_window),)),
    )
    monkeypatch.setattr(module, "issue_aggregate_series_key_v2", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(module, "open_verified_aggregate_series_v2", lambda *_args: object())
    monkeypatch.setattr(module, "bridge_verified_aggregate_series_v2", lambda _series: object())
    monkeypatch.setattr(
        module,
        "candidate_definition_for_verified_series",
        lambda *_args: object(),
    )


def test_vs0001_population_propagates_detector_cap_plus_one_without_truncation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from market_structure_lab.research import validation_v2 as module

    _stub_population_dependencies(monkeypatch, module)

    def reject_cap_plus_one(
        *_args: object,
        max_emitted_signals: int,
        **_kwargs: object,
    ) -> tuple[object, ...]:
        assert max_emitted_signals == 512
        raise ValueError("candidate signal emission exceeds the admitted event ceiling")

    monkeypatch.setattr(module, "detect_candidate_signals", reject_cap_plus_one)

    with pytest.raises(ValueError, match="signal emission.*ceiling"):
        module._find_vs0001_population_v2(  # noqa: SLF001
            sources=_stub_population_sources(),
            aggregate_budget=cast(Any, object()),
        )


def test_vs0001_population_rejects_duplicate_opportunity_and_eligible_cap_plus_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from market_structure_lab.research import validation_v2 as module

    _stub_population_dependencies(monkeypatch, module)
    base = datetime(2025, 2, 1, tzinfo=UTC)
    duplicate_opportunity = (
        _stub_population_signal("CS-1", base),
        _stub_population_signal("CS-2", base),
    )
    monkeypatch.setattr(
        module,
        "detect_candidate_signals",
        lambda *_args, **_kwargs: duplicate_opportunity,
    )
    with pytest.raises(ValueError, match="duplicate opportunity"):
        module._find_vs0001_population_v2(  # noqa: SLF001
            sources=_stub_population_sources(),
            aggregate_budget=cast(Any, object()),
        )

    eligible_cap_plus_one = tuple(
        _stub_population_signal(f"CS-{index:03d}", base + timedelta(hours=25 * index))
        for index in range(257)
    )
    monkeypatch.setattr(
        module,
        "detect_candidate_signals",
        lambda *_args, **_kwargs: eligible_cap_plus_one,
    )
    monkeypatch.setattr(
        module,
        "assign_development_event_v2",
        lambda **_kwargs: object(),
    )
    with pytest.raises(ValueError, match="eligible-event cap"):
        module._find_vs0001_population_v2(  # noqa: SLF001
            sources=_stub_population_sources(),
            aggregate_budget=cast(Any, object()),
        )


def test_vs0001_population_issues_no_reader_when_a_later_minute_path_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from market_structure_lab.research import validation_v2 as module

    base = datetime(2025, 2, 1, tzinfo=UTC)
    signals = (
        _stub_population_signal("CS-1", base),
        _stub_population_signal("CS-2", base + timedelta(hours=25)),
    )
    members = tuple(
        module._Vs0001PopulationMemberV2(  # noqa: SLF001
            aggregate_series=cast(Any, object()),
            candidate_series=cast(Any, object()),
            signal=cast(Any, signal),
            assignment=cast(Any, object()),
        )
        for signal in signals
    )
    folds = SimpleNamespace(
        outer_folds=tuple(
            SimpleNamespace(inner_folds=(object(), object(), object())) for _ in range(4)
        ),
        verify_original=lambda: None,
    )
    population = module._Vs0001PopulationV2(  # noqa: SLF001
        slot=_vs0001(),
        folds=cast(Any, folds),
        detected=tuple(
            (member.aggregate_series, member.candidate_series, member.signal) for member in members
        ),
        detected_signal_ids=tuple(signal.signal_id for signal in signals),
        eligible=members,
        eligible_signal_ids=tuple(signal.signal_id for signal in signals),
        retained=members,
        skipped_signal_ids=(),
        population_sha256="b" * 64,
    )
    over_cap_detected = (population.detected[0],) * 513
    over_cap_population = replace(
        population,
        detected=over_cap_detected,
        detected_signal_ids=tuple(item[2].signal_id for item in over_cap_detected),
    )
    with pytest.raises(ValueError, match="detected population exceeds"):
        module._verify_vs0001_population_v2(over_cap_population)  # noqa: SLF001
    monkeypatch.setattr(module, "_find_vs0001_population_v2", lambda **_kwargs: population)
    monkeypatch.setattr(
        module,
        "BoundaryRequestV2",
        lambda **kwargs: SimpleNamespace(**kwargs),
    )
    monkeypatch.setattr(
        module,
        "DevelopmentAccessAttemptLedgerV2",
        lambda **_kwargs: object(),
    )
    read_count = 0

    def fail_second_minute_path(*_args: object, **_kwargs: object) -> object:
        nonlocal read_count
        read_count += 1
        if read_count == 2:
            raise RuntimeError("second minute path failed")
        return object()

    monkeypatch.setattr(module, "read_verified_minute_path_v2", fail_second_minute_path)
    monkeypatch.setattr(module, "attach_development_outcome_v2", lambda *_args, **_kwargs: object())
    config: Any = SimpleNamespace(
        programme_id="VP-" + "1" * 64,
        access_ledger_identity=SimpleNamespace(value="AUDV2-" + "2" * 64),
    )
    sources: Any = SimpleNamespace(
        source_publication=SimpleNamespace(origin_sha256="3" * 64),
        coverage=object(),
        split=object(),
        boundary=object(),
        availability=object(),
        cost_authority=object(),
    )
    admitted = ValidationWorkDemand(
        candidates=1,
        events=512,
        outcomes=256,
        path_cells=256 * 24 * 60,
        outer_folds=4,
        inner_folds=3,
    )
    registered_before = set(module._VERIFIED_PUBLICATION_OUTCOME_READERS)  # noqa: SLF001

    with pytest.raises(RuntimeError, match="second minute path failed"):
        module._build_real_vs0001_reader_v2(  # noqa: SLF001
            config=config,
            sources=sources,
            budget=ValidationWorkBudget(),
            admitted_demand=admitted,
            authenticated_demand=admitted,
        )

    assert read_count == 2
    assert set(module._VERIFIED_PUBLICATION_OUTCOME_READERS) == registered_before  # noqa: SLF001


def test_vs0001_population_overlap_is_half_open_and_symbol_scoped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from market_structure_lab.research import validation_v2 as module

    _stub_population_dependencies(monkeypatch, module)
    monkeypatch.setattr(
        module,
        "assign_development_event_v2",
        lambda **_kwargs: object(),
    )
    base = datetime(2025, 2, 1, tzinfo=UTC)

    def population_for(signals: tuple[SimpleNamespace, ...]) -> Any:
        monkeypatch.setattr(
            module,
            "detect_candidate_signals",
            lambda *_args, **_kwargs: signals,
        )
        return module._find_vs0001_population_v2(  # noqa: SLF001
            sources=_stub_population_sources(),
            aggregate_budget=cast(Any, object()),
        )

    overlap = population_for(
        (
            _stub_population_signal("CS-1", base),
            _stub_population_signal("CS-2", base + timedelta(hours=12)),
        )
    )
    assert tuple(item.signal.signal_id for item in overlap.retained) == ("CS-1",)
    assert overlap.skipped_signal_ids == ("CS-2",)

    exact_boundary = population_for(
        (
            _stub_population_signal("CS-1", base),
            _stub_population_signal("CS-2", base + timedelta(hours=24)),
        )
    )
    assert tuple(item.signal.signal_id for item in exact_boundary.retained) == (
        "CS-1",
        "CS-2",
    )
    assert exact_boundary.skipped_signal_ids == ()

    cross_symbol = population_for(
        (
            _stub_population_signal("CS-1", base),
            _stub_population_signal("CS-2", base + timedelta(hours=12), symbol="ETHUSDT"),
        )
    )
    assert tuple(item.signal.signal_id for item in cross_symbol.retained) == (
        "CS-1",
        "CS-2",
    )
    assert cross_symbol.skipped_signal_ids == ()


def test_public_runner_rejects_nominal_boundary_subclasses_before_io() -> None:
    class ConfigSubclass(ValidationProgrammeConfigV2):
        pass

    class SourceSubclass(ValidationV2SourceBundle):
        pass

    class BypassBudget(ValidationWorkBudget):
        def preflight(self, *_args: object, **_kwargs: object) -> None:
            pytest.fail("budget subclass overrode the trusted preflight")

    class DemandSubclass(ValidationWorkDemand):
        pass

    config = object.__new__(ValidationProgrammeConfigV2)
    sources = object.__new__(ValidationV2SourceBundle)
    budget = ValidationWorkBudget()
    demand = ValidationWorkDemand()

    with pytest.raises(TypeError, match="config.*exact"):
        run_validation_programme_v2(
            config=object.__new__(ConfigSubclass),
            sources=sources,
            budget=budget,
            demand=demand,
        )
    with pytest.raises(TypeError, match="sources.*exact"):
        run_validation_programme_v2(
            config=config,
            sources=object.__new__(SourceSubclass),
            budget=budget,
            demand=demand,
        )
    with pytest.raises(TypeError, match="budget.*exact"):
        run_validation_programme_v2(
            config=config,
            sources=sources,
            budget=BypassBudget(),
            demand=demand,
        )
    with pytest.raises(TypeError, match="demand.*exact"):
        run_validation_programme_v2(
            config=config,
            sources=sources,
            budget=budget,
            demand=DemandSubclass(),
        )


def test_publication_outcome_reader_has_no_reusable_factory_or_registration_surface() -> None:
    from market_structure_lab.research import validation_v2 as module

    assert not hasattr(module, "_PUBLICATION_OUTCOME_READER_FACTORY")
    assert not hasattr(module, "_register_publication_outcome_reader_v2")
    before = dict(module._VERIFIED_PUBLICATION_OUTCOME_READERS)

    with pytest.raises(TypeError, match="verifier factory"):
        module.VerifiedPublicationOutcomeReaderV2(
            programme_id="VPV2-" + "a" * 64,
            split_sha256="b" * 64,
            cost_authority_sha256="c" * 64,
            source_publication_sha256="d" * 64,
            aggregate_publication_sha256="e" * 64,
            slot_ids=("VS-0001",),
            outcome_set_sha256="f" * 64,
            canonical_bytes=b"forged",
            _outcomes_by_slot={},
            _factory_token=object(),
        )

    assert module._VERIFIED_PUBLICATION_OUTCOME_READERS == before


def test_public_runner_rejects_nested_source_lookalike_before_property_access() -> None:
    from market_structure_lab.research.validation_v2_models import (
        AccessAuditLedgerIdentityV2,
        AggregatePublicationIdentityV2,
        CostAuthorityIdentityV2,
        DevelopmentSplitIdentityV2,
        PrecisionAuthorityIdentityV2,
        SourcePublicationIdentityV2,
        ValidationRosterIdentityV2,
    )

    class CoverageLookalike:
        @property
        def coverage_identity(self) -> object:
            pytest.fail("nested source lookalike property was accessed")

    sources = object.__new__(ValidationV2SourceBundle)
    object.__setattr__(sources, "coverage", CoverageLookalike())
    for field_name in (
        "split",
        "boundary",
        "availability",
        "source_publication",
        "aggregate_publication",
        "precision_authority",
        "cost_authority",
    ):
        object.__setattr__(sources, field_name, object())
    config = ValidationProgrammeConfigV2(
        implementation_checkpoint="a" * 40,
        coverage_identity=SourceCoverageIdentityV2.from_payload({"inert": "coverage"}),
        split_identity=DevelopmentSplitIdentityV2.from_payload({"inert": "split"}),
        source_identity=SourcePublicationIdentityV2.from_payload({"inert": "source"}),
        aggregate_identity=AggregatePublicationIdentityV2.from_payload({"inert": "aggregate"}),
        precision_identity=PrecisionAuthorityIdentityV2.from_payload({"inert": "precision"}),
        cost_identity=CostAuthorityIdentityV2.from_payload({"inert": "cost"}),
        roster_identity=ValidationRosterIdentityV2.from_payload({"inert": "roster"}),
        access_ledger_identity=AccessAuditLedgerIdentityV2.from_payload({"inert": "ledger"}),
        policy_identities=(),
        work_budget_sha256=ValidationWorkBudget().sha256,
    )

    with pytest.raises(TypeError, match="coverage.*exact"):
        run_validation_programme_v2(
            config=config,
            sources=sources,
            budget=ValidationWorkBudget(),
            demand=ValidationWorkDemand(),
        )


def test_public_runner_rejects_config_identity_subclass_before_value_access() -> None:
    from market_structure_lab.data.aggregate_publication_v2 import AggregatePublicationV2
    from market_structure_lab.data.validation_precision_authority_v2 import (
        ValidationPrecisionAuthorityV2,
    )
    from market_structure_lab.data.validation_source_v2 import (
        ScopedSourceAvailabilityV2,
        ValidationSourcePublicationV2,
    )
    from market_structure_lab.research.validation_v2_costs import VerifiedCostAuthorityV2
    from market_structure_lab.research.validation_v2_models import SourceCoveragePublicationV2
    from market_structure_lab.research.validation_v2_splits import (
        DevelopmentReadBoundaryV2,
        DevelopmentSplitPublicationV2,
    )

    class CoverageIdentitySubclass(SourceCoverageIdentityV2):
        @property
        def value(self) -> str:  # type: ignore[override]
            pytest.fail("config identity subclass value was accessed")

    config = object.__new__(ValidationProgrammeConfigV2)
    object.__setattr__(config, "coverage_identity", object.__new__(CoverageIdentitySubclass))
    for field_name in (
        "split_identity",
        "source_identity",
        "aggregate_identity",
        "precision_identity",
        "cost_identity",
        "roster_identity",
        "access_ledger_identity",
    ):
        object.__setattr__(config, field_name, object())

    sources = object.__new__(ValidationV2SourceBundle)
    for field_name, parent_type in (
        ("coverage", SourceCoveragePublicationV2),
        ("split", DevelopmentSplitPublicationV2),
        ("boundary", DevelopmentReadBoundaryV2),
        ("availability", ScopedSourceAvailabilityV2),
        ("source_publication", ValidationSourcePublicationV2),
        ("aggregate_publication", AggregatePublicationV2),
        ("precision_authority", ValidationPrecisionAuthorityV2),
        ("cost_authority", VerifiedCostAuthorityV2),
    ):
        object.__setattr__(sources, field_name, object.__new__(parent_type))

    with pytest.raises(TypeError, match="coverage_identity.*exact"):
        run_validation_programme_v2(
            config=config,
            sources=sources,
            budget=ValidationWorkBudget(),
            demand=ValidationWorkDemand(),
        )


def test_public_runner_rejects_runner_version_substitution_before_inputs() -> None:
    class VersionSubclass(str):
        pass

    inputs = {
        "config": object.__new__(ValidationProgrammeConfigV2),
        "sources": object.__new__(ValidationV2SourceBundle),
        "budget": ValidationWorkBudget(),
        "demand": ValidationWorkDemand(),
    }
    for version in ("forged-runner", VersionSubclass("phase5-validation-slot-runner-v2")):
        with pytest.raises(ValueError, match="runner_version.*frozen"):
            run_validation_programme_v2(**inputs, runner_version=version)  # type: ignore[arg-type]


def _long_development_chain(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[Any, ...]:
    import test_aggregate_publication_v2 as fixtures

    real_datetime = fixtures.datetime

    def long_datetime(*args: Any, **kwargs: Any) -> datetime:
        value = real_datetime(*args, **kwargs)
        if value == datetime(2025, 1, 7, tzinfo=UTC):
            return datetime(2025, 2, 6, tzinfo=UTC)
        return value

    monkeypatch.setattr(fixtures, "datetime", long_datetime)
    return fixtures.v2_chain.__wrapped__(tmp_path, monkeypatch)  # type: ignore[attr-defined]


def _real_unavailable_archive_parents(
    *,
    boundary: Any,
    availability: Any,
    root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[Any, Path, Any]:
    from market_structure_lab.data import binance_archive_v2 as archive_module
    from market_structure_lab.data.binance_archive_v2 import (
        ArchiveBudgetsV2,
        ArchiveNetworkUnavailable,
        acquire_binance_archives_v2,
        freeze_binance_archive_requests_v2,
        verify_binance_archive_acquisition_v2,
        verify_binance_archive_request_manifest_v2,
    )

    manifest_path = root / "archive-manifest.json"
    manifest = freeze_binance_archive_requests_v2(
        boundary=boundary,
        source_availability=availability,
        budgets=ArchiveBudgetsV2.testing(max_requests=20_000),
        output=manifest_path,
    )
    assert {request.symbol for request in manifest.requests} == set(boundary.allowed_symbols)
    assert not {"BTCUSDT", "ETHUSDT"} & {request.symbol for request in manifest.requests}
    assert (
        verify_binance_archive_request_manifest_v2(
            manifest,
            boundary=boundary,
            source_availability=availability,
            publication_path=manifest_path,
        )
        is manifest
    )
    cache_root = root / "archive-cache"
    cache_root.mkdir()
    monkeypatch.setattr(
        archive_module,
        "_download_small_with_retries",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            ArchiveNetworkUnavailable("bounded real-slice archive unavailable")
        ),
    )
    acquisition = acquire_binance_archives_v2(
        boundary=boundary,
        manifest=manifest,
        manifest_path=manifest_path,
        audit_ledger_root=root / "archive-audit",
        cache_root=cache_root,
        output_root=root / "archive-publication",
    )
    assert acquisition.status == "unavailable"
    assert (
        verify_binance_archive_acquisition_v2(
            acquisition,
            boundary=boundary,
            manifest=manifest,
            manifest_path=manifest_path,
        )
        is acquisition
    )
    return manifest, manifest_path, acquisition


def _vs0001_minute_rows(
    boundary: Any,
    *,
    breakout_hours: tuple[int, ...] = (72,),
    event_interval_indexes: tuple[int, ...] = (1,),
) -> tuple[tuple[CanonicalMinuteRowV2, ...], ...]:
    if (
        tuple(sorted(set(breakout_hours))) != breakout_hours
        or any(hour < 72 for hour in breakout_hours)
        or any(right <= left for left, right in zip(breakout_hours, breakout_hours[1:]))
    ):
        raise ValueError("breakout_hours must be unique, sorted, and increasing")
    if tuple(sorted(set(event_interval_indexes))) != event_interval_indexes or any(
        not 0 <= index < len(boundary.allowed_intervals) for index in event_interval_indexes
    ):
        raise ValueError("event_interval_indexes must be unique sorted allowed indexes")
    requests: list[tuple[CanonicalMinuteRowV2, ...]] = []
    for symbol in boundary.allowed_symbols:
        for interval_index, interval in enumerate(boundary.allowed_intervals):
            selected_breakouts = breakout_hours if interval_index in event_interval_indexes else ()
            hours = max(selected_breakouts, default=72) + 28 if selected_breakouts else 4
            rows: list[CanonicalMinuteRowV2] = []
            level = Decimal("100")
            for hour in range(hours):
                if hour not in selected_breakouts:
                    open_, high, low, close = (
                        level,
                        level + 1,
                        level - 1,
                        level,
                    )
                else:
                    next_level = level * Decimal("1.2")
                    open_, high, low, close = (
                        level,
                        next_level + 1,
                        level - 1,
                        next_level,
                    )
                    level = next_level
                for minute in range(60):
                    rows.append(
                        CanonicalMinuteRowV2(
                            timestamp=interval.start + timedelta(hours=hour, minutes=minute),
                            symbol=symbol,
                            timeframe="1m",
                            open=open_ if minute == 0 else close,
                            high=high,
                            low=low,
                            close=close,
                            volume=Decimal("1"),
                        )
                    )
            requests.append(tuple(rows))
    return tuple(requests)


def _real_public_programme_inputs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    breakout_hours: tuple[int, ...] = (72,),
    event_interval_indexes: tuple[int, ...] = (1,),
) -> tuple[
    ValidationProgrammeConfigV2,
    ValidationV2SourceBundle,
    ValidationWorkBudget,
    ValidationWorkDemand,
]:
    import test_aggregate_publication_v2 as aggregate_tests
    import test_validation_precision_authority_v2 as precision_tests
    from market_structure_lab.research.validation_v2_costs import (
        verified_cost_authority_bytes_v2,
    )

    coverage, split, boundary, availability = _long_development_chain(
        tmp_path,
        monkeypatch,
    )
    chain = coverage, split, boundary, availability
    minute = aggregate_tests._publish_minute(  # noqa: SLF001
        chain,
        tmp_path,
        rows=_vs0001_minute_rows(
            boundary,
            breakout_hours=breakout_hours,
            event_interval_indexes=event_interval_indexes,
        ),
        suffix="-real-vs0001",
    )
    aggregate = aggregate_tests._publish_aggregate(  # noqa: SLF001
        chain,
        minute,
        tmp_path / "aggregate-real-vs0001",
    )

    precision_root = tmp_path / "precision-inputs"
    precision_root.mkdir()
    precision_source, precision_verifier = precision_tests._historical_evidence(  # noqa: SLF001
        precision_root,
        boundary,
        monkeypatch,
    )
    precision = publish_validation_precision_authority_v2(
        request=precision_tests._request(  # noqa: SLF001
            boundary,
            PrecisionSourceKindV2.PUBLISHER_HISTORICAL_SCHEDULE,
            limitation=None,
        ),
        coverage=coverage,
        split=split,
        boundary=boundary,
        availability=availability,
        minute_publication=minute,
        aggregate_publication=aggregate,
        authority_source_path=precision_source,
        verifier_evidence_path=precision_verifier,
        output_root=tmp_path / "precision-real-vs0001",
    )

    cost_root = tmp_path / "cost-inputs"
    cost_root.mkdir()
    archive_manifest, archive_manifest_path, archive_acquisition = (
        _real_unavailable_archive_parents(
            boundary=boundary,
            availability=availability,
            root=cost_root,
            monkeypatch=monkeypatch,
        )
    )
    cost = publish_validation_cost_authority_v2(
        source_publication=minute,
        aggregate_publication=aggregate,
        coverage=coverage,
        split=split,
        boundary=boundary,
        availability=availability,
        archive_manifest=archive_manifest,
        archive_manifest_path=archive_manifest_path,
        archive_acquisition=archive_acquisition,
        output_root=tmp_path / "cost-real-vs0001",
    )
    assert verified_cost_authority_bytes_v2(cost) == cost.canonical_bytes

    sources = ValidationV2SourceBundle(
        coverage=coverage,
        split=split,
        boundary=boundary,
        availability=availability,
        source_publication=minute,
        aggregate_publication=aggregate,
        precision_authority=precision,
        cost_authority=cost,
    )
    budget = ValidationWorkBudget()
    demand = ValidationWorkDemand(
        source_rows=minute.row_count,
        source_bytes=minute.byte_count,
        aggregate_bars=aggregate.row_count,
        symbols=len(boundary.allowed_symbols),
        ranges=len(boundary.allowed_intervals),
        candidates=1,
        events=512,
        outcomes=256,
        path_cells=256 * 24 * 60,
        outer_folds=4,
        inner_folds=3,
    )
    config = ValidationProgrammeConfigV2(
        implementation_checkpoint="b" * 40,
        coverage_identity=coverage.coverage_identity,
        split_identity=split.split_identity,
        source_identity=minute.source_publication_identity,
        aggregate_identity=aggregate.aggregate_identity,
        precision_identity=precision.precision_authority_identity,
        cost_identity=cost.cost_identity,
        roster_identity=ValidationRosterIdentityV2.from_payload(
            [slot.to_dict() for slot in VALIDATION_SLOT_ROSTER]
        ),
        access_ledger_identity=development_access_ledger_identity_v2(sources),
        policy_identities=(
            PHASE5_BOOTSTRAP_HOLM_POLICY_IDENTITY,
            PHASE5_VS0001_POPULATION_POLICY_IDENTITY,
        ),
        work_budget_sha256=budget.sha256,
    )
    assert minute.final_rows == aggregate.final_rows == 0
    assert minute.final_access_records == aggregate.final_access_records == 0
    return config, sources, budget, demand


def test_public_runner_executes_one_real_vs0001_development_outcome(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from market_structure_lab.research import validation_v2 as module

    config, sources, budget, demand = _real_public_programme_inputs(
        tmp_path,
        monkeypatch,
    )
    issued_readers: list[object] = []
    authority_verification_calls = 0
    real_issuer = module._issue_publication_outcome_reader_v2  # noqa: SLF001
    real_authority_verifier = module.VerifiedPublicationOutcomeReaderV2.verify_original

    def capture_reader(**kwargs: object) -> object:
        reader = real_issuer(**kwargs)  # type: ignore[arg-type]
        issued_readers.append(reader)
        return reader

    def count_authority_verification(self: object) -> object:
        nonlocal authority_verification_calls
        authority_verification_calls += 1
        return real_authority_verifier(self)  # type: ignore[arg-type]

    monkeypatch.setattr(module, "_issue_publication_outcome_reader_v2", capture_reader)
    monkeypatch.setattr(
        module.VerifiedPublicationOutcomeReaderV2,
        "verify_original",
        count_authority_verification,
    )
    monkeypatch.setattr(
        module,
        "_issue_fixture_outcome_reader_v2",
        lambda *_args, **_kwargs: pytest.fail(
            "public runner called the fixture-only outcome reader"
        ),
    )

    run = run_validation_programme_v2(
        config=config,
        sources=sources,
        budget=budget,
        demand=demand,
    )

    assert len(issued_readers) == 1
    assert authority_verification_calls == 2
    assert run.verify_original() is run
    assert authority_verification_calls == 3
    reader = issued_readers[0]
    forged_fields = run.results[0].to_dict()
    for key in (
        "parent_slot_id",
        "result_sha256",
        "slot_computation_result_sha256",
    ):
        forged_fields.pop(key)
    forged_fields.update(
        execution_status="completed",
        decision="promoted",
        computation_completed=True,
        reason="caller-created promotion",
        p_value=0.0,
        metrics={"fabricated": 1},
    )
    forged_root = tmp_path / "forged-receipts"
    forged_root.mkdir()
    with pytest.raises(TypeError, match="concrete computation factory"):
        module._issue_validation_slot_result_v2(  # type: ignore[arg-type]  # noqa: SLF001
            evidence_verifier=lambda: reader,
            retained_evidence=(config, reader),
            evidence_authority=reader,
            evidence_authority_verifier=reader.verify_original,  # type: ignore[attr-defined]
            receipt_authority_config=config,
            **forged_fields,  # pyright: ignore[reportArgumentType]
        )
    assert not tuple(forged_root.iterdir())
    outcomes = reader.read_slot(  # type: ignore[attr-defined]
        _vs0001(),
        programme_id=config.programme_id,
        split_sha256=sources.boundary.boundary_sha256,
        cost_authority_sha256=sources.cost_authority.cost_identity.value.removeprefix("CSTV2-"),
    )
    assert len(outcomes) == 1
    outcome = outcomes[0]
    assert isinstance(outcome, DevelopmentOutcomeRowV2)
    assert outcome.verify_original() is outcome
    assert outcome.candidate_slot_id == "VS-0001"
    assert outcome.path_row_count == 24 * 60
    assert outcome.entry_time == outcome.information_cutoff
    assert outcome.exit_time == outcome.entry_time + timedelta(hours=24)
    assert outcome.net_return is None
    assert outcome.incomplete_cost_dimensions
    assert outcome.entry_price == outcome.exit_price == Decimal("120")
    assert outcome.gross_signed_return == Decimal("0")
    assert outcome.mfe == Decimal("0.008333333333333333333333333")
    assert outcome.mae == Decimal("-0.0083333333333333333333333333")
    assert (
        outcome.aggregate_publication_sha256
        == hashlib.sha256(sources.aggregate_publication.canonical_bytes).hexdigest()
    )
    assert outcome.final_access_records == 0
    assert sources.source_publication.final_access_records == 0
    assert sources.aggregate_publication.final_access_records == 0

    candidate_inputs = derive_verified_vs0001_candidate_inputs_v2(reader)  # type: ignore[arg-type]
    assert candidate_inputs.verify_original() is candidate_inputs
    assert candidate_inputs.slot_id == "VS-0001"
    assert candidate_inputs.final_holdout_access_count == 0
    assert candidate_inputs.unavailable_outcomes == ()
    [candidate_row] = candidate_inputs.rows
    assert candidate_row.event_id == outcome.signal_id
    assert candidate_row.control_role == "candidate"
    assert candidate_row.detector_role == "moving_average_crossover"
    assert candidate_row.entry_price == 120.0
    assert candidate_row.exit_price == 120.0
    assert candidate_row.delayed_entry_price == 120.0
    assert candidate_row.delayed_exit_price == 120.0
    assert candidate_row.delayed_entry_time == outcome.entry_time + timedelta(hours=1)
    assert candidate_row.delayed_exit_time == outcome.exit_time + timedelta(hours=1)
    assert candidate_row.prior_completed_close_return == pytest.approx(0.2)
    with pytest.raises(TypeError, match="verifier factory"):
        replace(candidate_inputs, rows=candidate_inputs.rows)

    control_inputs = derive_verified_vs0001_control_inputs_v2(candidate_inputs)
    assert control_inputs.verify_original() is control_inputs
    assert control_inputs.programme_id == candidate_inputs.programme_id
    assert control_inputs.slot_id == "VS-0001"
    assert control_inputs.candidate_input_set_sha256 == candidate_inputs.input_set_sha256
    assert control_inputs.source_publication_sha256 == candidate_inputs.source_publication_sha256
    assert control_inputs.aggregate_publication_sha256 == (
        candidate_inputs.aggregate_publication_sha256
    )
    assert control_inputs.final_holdout_access_count == 0
    assert len(control_inputs.unconditional_rows) == 1
    assert control_inputs.persistence_rows == ()
    assert len(control_inputs.unavailable_strata) == 1
    assert control_inputs.unavailable_strata[0][1] == "persistence_donor_shortage:1"
    control_row = control_inputs.unconditional_rows[0]
    assert control_row.control_role == "unconditional"
    assert control_row.event_id != candidate_row.event_id
    assert control_row.row_identity != candidate_row.row_identity
    assert control_row.legal_entry_time != candidate_row.legal_entry_time
    assert control_row.label_end <= candidate_row.label_start
    assert (
        control_row.symbol,
        control_row.fold_id,
        control_row.utc_week_start,
        control_row.direction,
        control_row.horizon_hours,
        control_row.timeframe,
        control_row.programme_id,
        control_row.publication_sha256,
        control_row.segment_id,
        control_row.component,
        control_row.block_id,
    ) == (
        candidate_row.symbol,
        candidate_row.fold_id,
        candidate_row.utc_week_start,
        candidate_row.direction,
        candidate_row.horizon_hours,
        candidate_row.timeframe,
        candidate_row.programme_id,
        candidate_row.publication_sha256,
        candidate_row.segment_id,
        candidate_row.component,
        candidate_row.block_id,
    )
    with pytest.raises(TypeError, match="verifier factory"):
        replace(
            control_inputs,
            unconditional_rows=control_inputs.unconditional_rows,
        )

    original_delayed_exit = candidate_row.delayed_exit_price
    object.__setattr__(candidate_row, "delayed_exit_price", 999.0)
    try:
        with pytest.raises(ValueError, match="candidate input"):
            candidate_inputs.verify_original()
    finally:
        object.__setattr__(candidate_row, "delayed_exit_price", original_delayed_exit)

    original_control_exit = control_row.exit_price
    object.__setattr__(control_row, "exit_price", 999.0)
    try:
        with pytest.raises(ValueError, match="control input"):
            control_inputs.verify_original()
    finally:
        object.__setattr__(control_row, "exit_price", original_control_exit)

    assert run.execution_scope == "development-only-full-roster"
    assert run.planned_slot_count == 1_104
    assert run.executed_slot_count == 1_104
    assert run.roster_complete is True
    assert run.scientific_terminal is True
    assert run.execution_status == "failed"
    assert run.decision == "not_evaluated"
    assert run.final_holdout_access_count == 0
    with pytest.raises(TypeError, match="computation factory"):
        replace(run, holm=run.holm)
    assert len(run.results) == 1_104
    assert len(run.holm) == 64
    assert {item.alpha for item in run.holm} == {0.01}
    assert all(item.effective_p_value == 1.0 for item in run.holm)
    assert sum(item.execution_status == "completed" for item in run.results) == 1
    assert sum(item.execution_status == "failed" for item in run.results) == 1_103

    unavailable_root = run.results[1]
    assert unavailable_root.parent_result_sha256 is None
    original_gross_return = outcome.gross_signed_return
    object.__setattr__(outcome, "gross_signed_return", Decimal("1"))
    try:
        with pytest.raises(ValueError):
            reader.verify_original()  # type: ignore[attr-defined]
        with pytest.raises(ValueError):
            verify_original_validation_slot_result_v2(unavailable_root)
    finally:
        object.__setattr__(outcome, "gross_signed_return", original_gross_return)
    original_source_bytes = sources.source_publication.canonical_bytes
    object.__setattr__(sources.source_publication, "canonical_bytes", b"forged")
    try:
        with pytest.raises(ValueError):
            verify_original_validation_slot_result_v2(unavailable_root)
    finally:
        object.__setattr__(
            sources.source_publication,
            "canonical_bytes",
            original_source_bytes,
        )

    receipt_root = tmp_path / "validation-receipts"
    receipt_root.mkdir()
    receipts = publish_validation_v2_receipts(receipt_root, run.results)
    report = verify_validation_programme_v2(
        run.results,
        receipts,
        runner_version=run.results[0].runner_version,
    )
    assert report.planned_slots == report.attempted_slots == 1_104
    assert report.completed_slot_computations == 1
    assert report.inconclusive_slot_computations == 1
    assert report.not_evaluated_slots == report.failed_slots == 1_103
    assert report.receipt_count == report.attempted_attempts == 1_104

    result = next(item for item in run.results if item.slot_id == "VS-0001")
    assert result.execution_status == "completed"
    assert result.decision == "inconclusive"
    assert result.p_value is None
    assert result.metrics["event_count"] == 1
    assert verify_original_validation_slot_result_v2(result) is result
    real_compute = module._compute_real_vs0001_material_v2  # noqa: SLF001

    def fabricated_replay(**kwargs: object) -> object:
        return replace(real_compute(**kwargs), reason="fabricated replay")  # type: ignore[arg-type]

    monkeypatch.setattr(module, "_compute_real_vs0001_material_v2", fabricated_replay)
    with pytest.raises(ValueError, match="computation replay differs"):
        verify_original_validation_slot_result_v2(result)


def test_public_runner_executes_three_ordered_vs0001_development_outcomes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from market_structure_lab.research import validation_v2 as module

    config, sources, budget, demand = _real_public_programme_inputs(
        tmp_path,
        monkeypatch,
        breakout_hours=(72,),
        event_interval_indexes=(1, 2, 3),
    )
    issued_readers: list[module.VerifiedPublicationOutcomeReaderV2] = []
    real_issuer = module._issue_publication_outcome_reader_v2  # noqa: SLF001

    def capture_reader(**kwargs: object) -> module.VerifiedPublicationOutcomeReaderV2:
        reader = real_issuer(**kwargs)  # type: ignore[arg-type]
        issued_readers.append(reader)
        return reader

    monkeypatch.setattr(module, "_issue_publication_outcome_reader_v2", capture_reader)

    run = run_validation_programme_v2(
        config=config,
        sources=sources,
        budget=budget,
        demand=demand,
    )

    [reader] = issued_readers
    outcomes = reader.read_slot(
        _vs0001(),
        programme_id=config.programme_id,
        split_sha256=sources.boundary.boundary_sha256,
        cost_authority_sha256=sources.cost_authority.cost_identity.value.removeprefix("CSTV2-"),
    )
    assert len(outcomes) == 3
    assert tuple(outcome.entry_time for outcome in outcomes) == tuple(
        sorted(outcome.entry_time for outcome in outcomes)
    )
    assert len({outcome.signal_id for outcome in outcomes}) == 3
    assert all(outcome.path_row_count == 24 * 60 for outcome in outcomes)
    result = next(item for item in run.results if item.slot_id == "VS-0001")
    assert result.execution_status == "completed"
    assert result.decision == "inconclusive"
    assert result.metrics["event_count"] == 3
    assert result.metrics["outcome_count"] == 3
    assert result.metrics["detected_event_count"] == 3
    assert result.metrics["eligible_event_count"] == 3
    assert result.metrics["skipped_overlap_count"] == 0
    assert result.metrics["path_cells"] == 3 * 24 * 60
    assert sum(item.execution_status == "completed" for item in run.results) == 1
    assert run.final_holdout_access_count == 0

    registration = module._VERIFIED_PUBLICATION_OUTCOME_READERS[id(reader)]  # noqa: SLF001
    population = registration.population
    original_detected_ids = population.detected_signal_ids
    object.__setattr__(population, "detected_signal_ids", tuple(reversed(original_detected_ids)))
    try:
        with pytest.raises(ValueError, match="detected population identities"):
            reader.verify_original()
        with pytest.raises(ValueError):
            verify_original_validation_slot_result_v2(result)
    finally:
        object.__setattr__(population, "detected_signal_ids", original_detected_ids)

    original_mapping = reader._outcomes_by_slot  # noqa: SLF001
    object.__setattr__(
        reader,
        "_outcomes_by_slot",
        MappingProxyType({"VS-0001": tuple(reversed(outcomes))}),
    )
    try:
        with pytest.raises(ValueError):
            reader.verify_original()
        with pytest.raises(ValueError):
            verify_original_validation_slot_result_v2(result)
    finally:
        object.__setattr__(reader, "_outcomes_by_slot", original_mapping)


def test_public_runner_authenticates_empty_vs0001_population_without_minute_reads(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from market_structure_lab.research import validation_v2 as module

    config, sources, budget, demand = _real_public_programme_inputs(
        tmp_path,
        monkeypatch,
        breakout_hours=(),
    )
    monkeypatch.setattr(
        module,
        "read_verified_minute_path_v2",
        lambda *_args, **_kwargs: pytest.fail("empty population opened minute outcomes"),
    )

    run = run_validation_programme_v2(
        config=config,
        sources=sources,
        budget=budget,
        demand=demand,
    )

    result = next(item for item in run.results if item.slot_id == "VS-0001")
    assert result.execution_status == "failed"
    assert result.decision == "not_evaluated"
    assert result.metrics["event_count"] == 0
    assert result.metrics["outcome_count"] == 0
    assert result.metrics["path_cells"] == 0
    assert run.scientific_terminal is True
    assert run.final_holdout_access_count == 0


def test_source_bundle_construction_is_passive_until_runner_budget_admission(
    v2_chain: tuple[Any, ...],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from test_validation_v2_public_runner import public_programme_inputs

    _config, sources, _budget, _demand = public_programme_inputs(
        v2_chain,
        tmp_path,
        monkeypatch,
    )
    monkeypatch.setattr(
        ValidationV2SourceBundle,
        "revalidate",
        lambda _self: pytest.fail(
            "source bundle constructor performed I/O before runner budget admission"
        ),
    )

    passive = ValidationV2SourceBundle(
        coverage=sources.coverage,
        split=sources.split,
        boundary=sources.boundary,
        availability=sources.availability,
        source_publication=sources.source_publication,
        aggregate_publication=sources.aggregate_publication,
        precision_authority=sources.precision_authority,
        cost_authority=sources.cost_authority,
    )

    assert passive.source_publication is sources.source_publication
    assert passive.aggregate_publication is sources.aggregate_publication


def test_public_runner_rejects_over_budget_path_demand_before_any_minute_read(
    v2_chain: tuple[Any, ...],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from market_structure_lab.research import validation_v2 as module
    from test_validation_v2_public_runner import public_programme_inputs

    config, sources, budget, demand = public_programme_inputs(
        v2_chain,
        tmp_path,
        monkeypatch,
    )
    over_budget = replace(demand, path_cells=budget.max_path_cells + 1)
    monkeypatch.setattr(
        ValidationV2SourceBundle,
        "revalidate",
        lambda _self: pytest.fail("over-budget path demand revalidated source parents"),
    )
    monkeypatch.setattr(
        module,
        "read_verified_minute_path_v2",
        lambda *_args, **_kwargs: pytest.fail("over-budget path demand opened minute rows"),
        raising=False,
    )

    with pytest.raises(ValidationWorkBudgetViolation, match="path_cells"):
        run_validation_programme_v2(
            config=config,
            sources=sources,
            budget=budget,
            demand=over_budget,
        )


def test_public_runner_rejects_underdeclared_physical_demand_before_aggregate_iteration(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from market_structure_lab.research import validation_v2 as module

    config, sources, budget, demand = _real_public_programme_inputs(
        tmp_path,
        monkeypatch,
    )
    underdeclared = replace(demand, source_rows=demand.source_rows - 1)
    monkeypatch.setattr(
        ValidationV2SourceBundle,
        "revalidate",
        lambda _self: pytest.fail("underdeclared physical demand revalidated source parents"),
    )
    monkeypatch.setattr(
        module,
        "open_verified_aggregate_series_v2",
        lambda *_args, **_kwargs: pytest.fail(
            "underdeclared physical demand opened aggregate rows"
        ),
    )

    with pytest.raises(ValidationWorkBudgetViolation, match="source_rows"):
        run_validation_programme_v2(
            config=config,
            sources=sources,
            budget=budget,
            demand=underdeclared,
        )


def test_public_runner_rejects_unadmitted_candidate_before_aggregate_open(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from market_structure_lab.research import validation_v2 as module

    config, sources, budget, demand = _real_public_programme_inputs(
        tmp_path,
        monkeypatch,
    )
    underdeclared = replace(demand, candidates=0)
    monkeypatch.setattr(
        module,
        "open_verified_aggregate_series_v2",
        lambda *_args, **_kwargs: pytest.fail("unadmitted candidate opened aggregate rows"),
    )

    with pytest.raises(ValidationWorkBudgetViolation, match="candidates"):
        run_validation_programme_v2(
            config=config,
            sources=sources,
            budget=budget,
            demand=underdeclared,
        )


def test_public_runner_rejects_mutated_source_counts_before_observation_read(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from market_structure_lab.data import validation_source_v2 as source_module

    config, sources, budget, demand = _real_public_programme_inputs(
        tmp_path,
        monkeypatch,
    )
    object.__setattr__(
        sources.source_publication,
        "row_count",
        sources.source_publication.row_count - 1,
    )
    monkeypatch.setattr(
        source_module,
        "read_bounded_regular",
        lambda *_args, **_kwargs: pytest.fail("mutated source metadata opened observations"),
    )

    with pytest.raises(ValueError, match="metadata|row counts"):
        run_validation_programme_v2(
            config=config,
            sources=sources,
            budget=budget,
            demand=demand,
        )


def test_public_runner_rejects_mutated_aggregate_counts_before_artifact_read(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from market_structure_lab.data import aggregate_publication_v2 as aggregate_module
    from market_structure_lab.data import validation_source_v2 as source_module

    config, sources, budget, demand = _real_public_programme_inputs(
        tmp_path,
        monkeypatch,
    )
    object.__setattr__(
        sources.aggregate_publication,
        "row_count",
        sources.aggregate_publication.row_count - 1,
    )

    def no_read(*_args: object, **_kwargs: object) -> None:
        pytest.fail("mutated aggregate metadata opened publication artifacts")

    monkeypatch.setattr(source_module, "read_bounded_regular", no_read)
    monkeypatch.setattr(aggregate_module, "read_bounded_regular", no_read)

    with pytest.raises(ValueError, match="serialization|identity"):
        run_validation_programme_v2(
            config=config,
            sources=sources,
            budget=budget,
            demand=demand,
        )


def test_budget_invariants_replay_before_metadata_and_allow_lower_ceilings(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from market_structure_lab.research import validation_v2 as module

    config, sources, budget, demand = _real_public_programme_inputs(
        tmp_path,
        monkeypatch,
    )
    source_metadata_verifier = module.verify_validation_source_publication_metadata_v2
    monkeypatch.setattr(
        module,
        "verify_validation_source_publication_metadata_v2",
        lambda *_args, **_kwargs: pytest.fail("invalid budget reached source metadata"),
    )
    oversized = replace(budget)
    object.__setattr__(oversized, "max_source_rows", budget.max_source_rows + 1)
    oversized_config = replace(config, work_budget_sha256=oversized.sha256)
    with pytest.raises(ValueError, match="authoritative ceiling"):
        run_validation_programme_v2(
            config=oversized_config,
            sources=sources,
            budget=oversized,
            demand=demand,
        )

    lower = replace(budget, max_source_rows=demand.source_rows - 1)
    lower_config = replace(config, work_budget_sha256=lower.sha256)
    with pytest.raises(ValidationWorkBudgetViolation, match="source_rows"):
        run_validation_programme_v2(
            config=lower_config,
            sources=sources,
            budget=lower,
            demand=demand,
        )

    invalid_demand = replace(demand)
    object.__setattr__(invalid_demand, "source_rows", -1)
    with pytest.raises(ValueError, match="source_rows"):
        run_validation_programme_v2(
            config=config,
            sources=sources,
            budget=budget,
            demand=invalid_demand,
        )

    invalid_config = replace(config)
    object.__setattr__(invalid_config, "implementation_checkpoint", "invalid")
    with pytest.raises(ValueError, match="implementation_checkpoint"):
        run_validation_programme_v2(
            config=invalid_config,
            sources=sources,
            budget=budget,
            demand=demand,
        )

    monkeypatch.setattr(
        module,
        "verify_validation_source_publication_metadata_v2",
        source_metadata_verifier,
    )
    monkeypatch.setattr(
        ValidationV2SourceBundle,
        "revalidate",
        lambda _self: pytest.fail("mutated authority reached source observations"),
    )
    precision_bytes = sources.precision_authority.canonical_bytes
    object.__setattr__(sources.precision_authority, "canonical_bytes", b"forged")
    with pytest.raises(ValueError, match="precision authority.*serialization|serialization"):
        run_validation_programme_v2(
            config=config,
            sources=sources,
            budget=budget,
            demand=demand,
        )
    object.__setattr__(sources.precision_authority, "canonical_bytes", precision_bytes)

    cost_bytes = sources.cost_authority.canonical_bytes
    object.__setattr__(sources.cost_authority, "canonical_bytes", b"forged")
    with pytest.raises(ValueError, match="cost authority.*serialization|serialization"):
        run_validation_programme_v2(
            config=config,
            sources=sources,
            budget=budget,
            demand=demand,
        )
    object.__setattr__(sources.cost_authority, "canonical_bytes", cost_bytes)

    class NestedLookalike:
        def __getattribute__(self, name: str) -> object:
            if name in {"value", "to_dict"}:
                pytest.fail("nested metadata lookalike accessor was invoked")
            return object.__getattribute__(self, name)

    source_identity = sources.source_publication.coverage_identity
    object.__setattr__(sources.source_publication, "coverage_identity", NestedLookalike())
    with pytest.raises(TypeError, match="non-exact nested metadata"):
        module.verify_validation_source_publication_metadata_v2(sources.source_publication)
    object.__setattr__(sources.source_publication, "coverage_identity", source_identity)

    aggregate_identity = sources.aggregate_publication.aggregate_identity
    object.__setattr__(sources.aggregate_publication, "aggregate_identity", NestedLookalike())
    with pytest.raises(TypeError, match="non-exact nested metadata"):
        module.verify_validation_aggregate_publication_metadata_v2(sources.aggregate_publication)
    object.__setattr__(sources.aggregate_publication, "aggregate_identity", aggregate_identity)

    precision_request = sources.precision_authority.request
    object.__setattr__(sources.precision_authority, "request", NestedLookalike())
    with pytest.raises(TypeError, match="non-exact nested metadata"):
        module.verify_validation_precision_authority_metadata_v2(sources.precision_authority)
    object.__setattr__(sources.precision_authority, "request", precision_request)

    class DecimalSubclass(Decimal):
        def __format__(self, format_spec: str, context: Context | None = None, /) -> str:
            pytest.fail("Decimal subclass formatting was invoked")

    first_precision = sources.precision_authority.entries[0]
    price_step = first_precision.price_step
    object.__setattr__(first_precision, "price_step", DecimalSubclass(price_step))
    with pytest.raises(TypeError, match="non-exact nested metadata"):
        module.verify_validation_precision_authority_metadata_v2(sources.precision_authority)
    object.__setattr__(first_precision, "price_step", price_step)

    authority_path = sources.precision_authority.authority_source_path
    object.__setattr__(sources.precision_authority, "authority_source_path", NestedLookalike())
    with pytest.raises(TypeError, match="non-exact nested metadata"):
        module.verify_validation_precision_authority_metadata_v2(sources.precision_authority)
    object.__setattr__(sources.precision_authority, "authority_source_path", authority_path)

    cost_dimensions = sources.cost_authority.dimensions
    object.__setattr__(sources.cost_authority, "dimensions", (NestedLookalike(),))
    with pytest.raises(TypeError, match="non-exact nested metadata"):
        module.verify_validation_cost_authority_metadata_v2(sources.cost_authority)
    object.__setattr__(sources.cost_authority, "dimensions", cost_dimensions)

    class FailOnIterationTuple(tuple[object, ...]):
        def __iter__(self):  # type: ignore[no-untyped-def]
            pytest.fail("metadata container was iterated before exact-type rejection")

    source_partitions = sources.source_publication.partitions
    object.__setattr__(
        sources.source_publication,
        "partitions",
        FailOnIterationTuple(source_partitions),
    )
    with pytest.raises(TypeError, match="non-exact nested metadata"):
        module.verify_validation_source_publication_metadata_v2(sources.source_publication)
    object.__setattr__(sources.source_publication, "partitions", source_partitions)

    aggregate_symbols = sources.aggregate_publication.allowed_symbols
    object.__setattr__(
        sources.aggregate_publication,
        "allowed_symbols",
        FailOnIterationTuple(aggregate_symbols),
    )
    with pytest.raises(TypeError, match="non-exact nested metadata"):
        module.verify_validation_aggregate_publication_metadata_v2(sources.aggregate_publication)
    object.__setattr__(sources.aggregate_publication, "allowed_symbols", aggregate_symbols)
    object.__setattr__(
        sources.aggregate_publication,
        "allowed_symbols",
        (aggregate_symbols[0],) * 100_001,
    )
    with pytest.raises(TypeError, match="non-exact nested metadata"):
        module.verify_validation_aggregate_publication_metadata_v2(sources.aggregate_publication)
    object.__setattr__(sources.aggregate_publication, "allowed_symbols", aggregate_symbols)

    requested_symbols = sources.precision_authority.request.requested_symbols
    object.__setattr__(
        sources.precision_authority.request,
        "requested_symbols",
        FailOnIterationTuple(requested_symbols),
    )
    with pytest.raises(TypeError, match="non-exact nested metadata"):
        module.verify_validation_precision_authority_metadata_v2(sources.precision_authority)
    object.__setattr__(
        sources.precision_authority.request,
        "requested_symbols",
        requested_symbols,
    )
    object.__setattr__(
        sources.precision_authority.request,
        "requested_symbols",
        (requested_symbols[0],) * 100_001,
    )
    with pytest.raises(TypeError, match="non-exact nested metadata"):
        module.verify_validation_precision_authority_metadata_v2(sources.precision_authority)
    object.__setattr__(
        sources.precision_authority.request,
        "requested_symbols",
        requested_symbols,
    )

    first_dimension = sources.cost_authority.dimensions[0]
    exclusions = first_dimension.exclusions
    object.__setattr__(
        first_dimension,
        "exclusions",
        FailOnIterationTuple(exclusions),
    )
    with pytest.raises(TypeError, match="non-exact nested metadata"):
        module.verify_validation_cost_authority_metadata_v2(sources.cost_authority)
    object.__setattr__(first_dimension, "exclusions", exclusions)
    reasons = sources.cost_authority.not_evaluated_reasons
    object.__setattr__(sources.cost_authority, "not_evaluated_reasons", ("missing",) * 10)
    with pytest.raises(TypeError, match="non-exact nested metadata"):
        module.verify_validation_cost_authority_metadata_v2(sources.cost_authority)
    object.__setattr__(sources.cost_authority, "not_evaluated_reasons", reasons)

    class PathLookalike:
        def __eq__(self, other: object) -> bool:
            pytest.fail("publication-root lookalike equality was invoked")

    aggregate_root = sources.aggregate_publication.publication_root
    object.__setattr__(sources.aggregate_publication, "publication_root", PathLookalike())
    with pytest.raises(TypeError, match="non-exact nested metadata"):
        module.verify_validation_aggregate_publication_metadata_v2(sources.aggregate_publication)
    object.__setattr__(sources.aggregate_publication, "publication_root", aggregate_root)

    cost_root = sources.cost_authority.publication_root
    object.__setattr__(sources.cost_authority, "publication_root", PathLookalike())
    with pytest.raises(TypeError, match="non-exact nested metadata"):
        module.verify_validation_cost_authority_metadata_v2(sources.cost_authority)
    object.__setattr__(sources.cost_authority, "publication_root", cost_root)

    object.__setattr__(
        sources.source_publication,
        "partitions",
        (source_partitions[0],) * 100_001,
    )
    with pytest.raises(TypeError, match="non-exact nested metadata"):
        module.verify_validation_source_publication_metadata_v2(sources.source_publication)
    object.__setattr__(sources.source_publication, "partitions", source_partitions)


def test_public_runner_rejects_mismatched_ledger_policy_before_minute_read(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from market_structure_lab.research import validation_v2 as module

    config, sources, budget, demand = _real_public_programme_inputs(
        tmp_path,
        monkeypatch,
    )
    mismatched = replace(
        config,
        access_ledger_identity=type(config.access_ledger_identity).from_payload(
            {"mismatch": "caller-created-ledger-policy"}
        ),
    )
    monkeypatch.setattr(
        ValidationV2SourceBundle,
        "revalidate",
        lambda _self: pytest.fail("mismatched ledger policy revalidated source parents"),
    )
    monkeypatch.setattr(
        module,
        "read_verified_minute_path_v2",
        lambda *_args, **_kwargs: pytest.fail(
            "mismatched ledger policy opened protected minute rows"
        ),
    )

    with pytest.raises(ValueError, match="access ledger identity"):
        run_validation_programme_v2(
            config=mismatched,
            sources=sources,
            budget=budget,
            demand=demand,
        )


def test_public_runner_rejects_mutated_aggregate_bytes_before_minute_read(
    v2_chain: tuple[Any, ...],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from market_structure_lab.research import validation_v2 as module
    from test_validation_v2_public_runner import public_programme_inputs

    config, sources, budget, demand = public_programme_inputs(
        v2_chain,
        tmp_path,
        monkeypatch,
    )
    partition = sources.aggregate_publication.members[0].partitions[0]
    partition_path = sources.aggregate_publication.publication_root / partition.path
    partition_path.write_bytes(partition_path.read_bytes() + b" ")
    monkeypatch.setattr(
        module,
        "read_verified_minute_path_v2",
        lambda *_args, **_kwargs: pytest.fail("mutated aggregate opened minute rows"),
        raising=False,
    )

    with pytest.raises(ValueError, match="aggregate|partition|bytes|publication"):
        run_validation_programme_v2(
            config=config,
            sources=sources,
            budget=budget,
            demand=demand,
        )


def test_public_runner_rejects_final_scope_substitution_before_minute_read(
    v2_chain: tuple[Any, ...],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from market_structure_lab.research import validation_v2 as module
    from test_validation_v2_public_runner import public_programme_inputs

    config, sources, budget, demand = public_programme_inputs(
        v2_chain,
        tmp_path,
        monkeypatch,
    )
    object.__setattr__(
        sources.boundary,
        "allowed_intervals",
        sources.boundary.forbidden_temporal_intervals,
    )
    monkeypatch.setattr(
        ValidationV2SourceBundle,
        "revalidate",
        lambda _self: pytest.fail("final-scope substitution revalidated source parents"),
    )
    monkeypatch.setattr(
        module,
        "read_verified_minute_path_v2",
        lambda *_args, **_kwargs: pytest.fail("final-scope substitution opened minute rows"),
        raising=False,
    )

    with pytest.raises((PermissionError, ValueError), match="boundary|scope|registered|identity"):
        run_validation_programme_v2(
            config=config,
            sources=sources,
            budget=budget,
            demand=demand,
        )
    assert sources.source_publication.final_access_records == 0
    assert sources.aggregate_publication.final_access_records == 0
