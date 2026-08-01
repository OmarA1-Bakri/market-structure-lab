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
    development_access_ledger_identity_v2,
    run_validation_programme_v2,
)
from market_structure_lab.research.validation_v2_costs import (
    publish_validation_cost_authority_v2,
)
from market_structure_lab.research.validation_v2_models import (
    PHASE5_BOOTSTRAP_HOLM_POLICY_IDENTITY,
    ValidationProgrammeConfigV2,
    ValidationRosterIdentityV2,
)

pytest_plugins = ("test_aggregate_publication_v2",)


def public_programme_inputs(v2_chain, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    import test_aggregate_publication_v2 as aggregate_tests
    import test_validation_precision_authority_v2 as precision_tests
    from test_validation_v2_real_vertical_slice import (
        _long_development_chain,
        _real_unavailable_archive_parents,
        _vs0001_minute_rows,
    )
    from market_structure_lab.research.validation_v2_costs import (
        verified_cost_authority_bytes_v2,
    )

    chain_root = tmp_path / "real-vs0001-chain"
    chain_root.mkdir()
    v2_chain = _long_development_chain(chain_root, monkeypatch)
    coverage, split, boundary, availability = v2_chain
    minute = aggregate_tests._publish_minute(
        v2_chain,
        tmp_path,
        rows=_vs0001_minute_rows(boundary),
        suffix="-public-runner",
    )
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
        output_root=tmp_path / "cost-public-runner",
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
        events=1,
        outcomes=1,
        path_cells=24 * 60,
        outer_folds=4,
        inner_folds=3,
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
        access_ledger_identity=development_access_ledger_identity_v2(sources),
        policy_identities=(PHASE5_BOOTSTRAP_HOLM_POLICY_IDENTITY,),
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

    config, sources, budget, demand = public_programme_inputs(
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

    assert any(result.metrics.get("event_count", 0) > 0 for result in run.results)  # type: ignore[operator]


def test_public_runner_rejects_over_budget_before_source_revalidation(
    v2_chain,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config, sources, budget, demand = public_programme_inputs(
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


def test_public_runner_rejects_missing_or_ambiguous_bootstrap_holm_amendment_before_reads(
    v2_chain,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    invalid_policy_identities = (
        (),
        (("MSL-P5-SR-001", "0" * 64),),
        (
            ("MSL-P5-SR-001", "0" * 64),
            PHASE5_BOOTSTRAP_HOLM_POLICY_IDENTITY,
        ),
    )
    config, sources, budget, demand = public_programme_inputs(
        v2_chain,
        tmp_path,
        monkeypatch,
    )
    monkeypatch.setattr(
        ValidationV2SourceBundle,
        "revalidate",
        lambda _self: pytest.fail("unbound amendment reached protected source revalidation"),
    )

    for policy_identities in invalid_policy_identities:
        unbound_config = replace(config, policy_identities=policy_identities)
        with pytest.raises(ValueError, match="MSL-P5-SR-001"):
            run_validation_programme_v2(
                config=unbound_config,
                sources=sources,
                budget=budget,
                demand=demand,
            )

    legacy_budget = replace(
        budget,
        bootstrap_draws=4_096,
        max_bootstrap_draws=4_096,
        max_bootstrap_cells=262_144,
    )
    legacy_config = replace(config, work_budget_sha256=legacy_budget.sha256)
    with pytest.raises(ValueError, match="amended.*bootstrap|4,800"):
        run_validation_programme_v2(
            config=legacy_config,
            sources=sources,
            budget=legacy_budget,
            demand=replace(demand, bootstrap_draws=4_096),
        )
