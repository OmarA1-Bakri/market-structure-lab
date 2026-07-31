from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
import hashlib
from pathlib import Path
from typing import Any

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
    run_validation_programme_v2,
)
from market_structure_lab.research.validation_v2_costs import (
    publish_validation_cost_authority_v2,
)
from market_structure_lab.research.validation_v2_models import (
    AccessAuditLedgerIdentityV2,
    ValidationProgrammeConfigV2,
    ValidationRosterIdentityV2,
)

pytest_plugins = ("test_aggregate_publication_v2",)


def _vs0001() -> ValidationSlot:
    return next(slot for slot in VALIDATION_SLOT_ROSTER if slot.slot_id == "VS-0001")


def _long_development_chain(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[Any, ...]:
    import test_aggregate_publication_v2 as fixtures

    real_datetime = fixtures.datetime

    def long_datetime(*args: object, **kwargs: object) -> datetime:
        value = real_datetime(*args, **kwargs)
        if value == datetime(2025, 1, 7, tzinfo=UTC):
            return datetime(2025, 2, 6, tzinfo=UTC)
        return value

    monkeypatch.setattr(fixtures, "datetime", long_datetime)
    return fixtures.v2_chain.__wrapped__(tmp_path, monkeypatch)


def _vs0001_minute_rows(boundary: Any) -> tuple[tuple[CanonicalMinuteRowV2, ...], ...]:
    requests: list[tuple[CanonicalMinuteRowV2, ...]] = []
    event_interval = boundary.allowed_intervals[1]
    for symbol in boundary.allowed_symbols:
        for interval in boundary.allowed_intervals:
            hours = 100 if interval == event_interval else 4
            rows: list[CanonicalMinuteRowV2] = []
            for hour in range(hours):
                if hour < 72:
                    open_, high, low, close = (
                        Decimal("100"),
                        Decimal("101"),
                        Decimal("99"),
                        Decimal("100"),
                    )
                elif hour == 72:
                    open_, high, low, close = (
                        Decimal("100"),
                        Decimal("121"),
                        Decimal("99"),
                        Decimal("120"),
                    )
                else:
                    open_, high, low, close = (
                        Decimal("120"),
                        Decimal("121"),
                        Decimal("119"),
                        Decimal("120"),
                    )
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
) -> tuple[
    ValidationProgrammeConfigV2,
    ValidationV2SourceBundle,
    ValidationWorkBudget,
    ValidationWorkDemand,
]:
    import test_aggregate_publication_v2 as aggregate_tests
    import test_validation_precision_authority_v2 as precision_tests
    import test_validation_v2_costs as cost_tests

    coverage, split, boundary, availability = _long_development_chain(
        tmp_path,
        monkeypatch,
    )
    chain = coverage, split, boundary, availability
    minute = aggregate_tests._publish_minute(  # noqa: SLF001
        chain,
        tmp_path,
        rows=_vs0001_minute_rows(boundary),
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
    cost_tests._patch_parent_verifiers(monkeypatch)  # noqa: SLF001
    cost_parents = cost_tests._parents(cost_root)  # noqa: SLF001
    cost = publish_validation_cost_authority_v2(
        source_publication=minute,
        aggregate_publication=aggregate,
        coverage=coverage,
        split=split,
        boundary=boundary,
        availability=availability,
        archive_manifest=cost_parents.manifest,
        archive_manifest_path=cost_parents.manifest_path,
        archive_acquisition=cost_parents.acquisition,
        output_root=tmp_path / "cost-real-vs0001",
    )

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
        events=1,
        outcomes=1,
        path_cells=24 * 60,
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
        access_ledger_identity=AccessAuditLedgerIdentityV2.from_payload(
            {"fixture": "real-vs0001-development-only-zero-final-access"}
        ),
        policy_identities=(),
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
    real_issuer = module._issue_publication_outcome_reader_v2  # noqa: SLF001

    def capture_reader(**kwargs: object) -> object:
        reader = real_issuer(**kwargs)  # type: ignore[arg-type]
        issued_readers.append(reader)
        return reader

    monkeypatch.setattr(module, "_issue_publication_outcome_reader_v2", capture_reader)
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
    reader = issued_readers[0]
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
    assert (
        outcome.aggregate_publication_sha256
        == hashlib.sha256(sources.aggregate_publication.canonical_bytes).hexdigest()
    )
    assert outcome.final_access_records == 0
    assert sources.source_publication.final_access_records == 0
    assert sources.aggregate_publication.final_access_records == 0

    result = next(item for item in run.results if item.slot_id == "VS-0001")
    assert result.execution_status == "completed"
    assert result.decision == "inconclusive"
    assert result.p_value is None
    assert result.metrics["event_count"] == 1


def test_source_bundle_construction_is_passive_until_runner_budget_admission(
    v2_chain: tuple[Any, ...],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from test_validation_v2_public_runner import _public_programme_inputs

    _config, sources, _budget, _demand = _public_programme_inputs(
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
    from test_validation_v2_public_runner import _public_programme_inputs

    config, sources, budget, demand = _public_programme_inputs(
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


def test_public_runner_rejects_mutated_aggregate_bytes_before_minute_read(
    v2_chain: tuple[Any, ...],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from market_structure_lab.research import validation_v2 as module
    from test_validation_v2_public_runner import _public_programme_inputs

    config, sources, budget, demand = _public_programme_inputs(
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
    from test_validation_v2_public_runner import _public_programme_inputs

    config, sources, budget, demand = _public_programme_inputs(
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
