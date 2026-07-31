from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from market_structure_lab.data.validation_precision_authority_v2 import (
    PrecisionSourceKindV2,
    publish_validation_precision_authority_v2,
)
from market_structure_lab.research.models import (
    VALIDATION_SLOT_ROSTER,
    ValidationWorkBudget,
    ValidationWorkBudgetViolation,
    ValidationWorkDemand,
)
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


def _public_programme_inputs(v2_chain, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    import test_aggregate_publication_v2 as aggregate_tests
    import test_validation_precision_authority_v2 as precision_tests
    import test_validation_v2_costs as cost_tests

    coverage, split, boundary, availability = v2_chain
    minute = aggregate_tests._publish_minute(v2_chain, tmp_path, suffix="-public-runner")
    aggregate = aggregate_tests._publish_aggregate(
        v2_chain,
        minute,
        tmp_path / "aggregate-public-runner",
    )

    precision_root = tmp_path / "precision-inputs"
    precision_root.mkdir()
    precision_source, precision_verifier = precision_tests._historical_evidence(
        precision_root,
        boundary,
        monkeypatch,
    )
    precision = publish_validation_precision_authority_v2(
        request=precision_tests._request(
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
        output_root=tmp_path / "precision-public-runner",
    )

    cost_root = tmp_path / "cost-inputs"
    cost_root.mkdir()
    cost_tests._patch_parent_verifiers(monkeypatch)
    cost_parents = cost_tests._parents(cost_root)
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
        output_root=tmp_path / "cost-public-runner",
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
    )
    config = ValidationProgrammeConfigV2(
        implementation_checkpoint="a" * 40,
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
            {"fixture": "development-only-zero-final-access"}
        ),
        policy_identities=(),
        work_budget_sha256=budget.sha256,
    )
    assert minute.row_count > 0
    assert aggregate.row_count > 0
    assert minute.final_rows == aggregate.final_rows == 0
    assert minute.final_access_records == aggregate.final_access_records == 0
    return config, sources, budget, demand


def test_public_runner_derives_nonempty_outcomes_from_verified_publications(
    v2_chain,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from market_structure_lab.research import validation_v2 as module

    config, sources, budget, demand = _public_programme_inputs(
        v2_chain,
        tmp_path,
        monkeypatch,
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

    assert any(result.metrics.get("event_count", 0) > 0 for result in run.results)


def test_public_runner_rejects_over_budget_before_source_revalidation(
    v2_chain,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config, sources, budget, demand = _public_programme_inputs(
        v2_chain,
        tmp_path,
        monkeypatch,
    )
    over_budget = replace(demand, source_rows=budget.max_source_rows + 1)
    monkeypatch.setattr(
        ValidationV2SourceBundle,
        "revalidate",
        lambda _self: pytest.fail("over-budget run revalidated source publications"),
    )

    with pytest.raises(ValidationWorkBudgetViolation, match="source_rows"):
        run_validation_programme_v2(
            config=config,
            sources=sources,
            budget=budget,
            demand=over_budget,
        )
