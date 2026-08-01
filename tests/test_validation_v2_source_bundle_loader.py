from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path

import pytest

from market_structure_lab.core.identity import hash_json
from market_structure_lab.research.models import ValidationWorkBudget, ValidationWorkDemand
from market_structure_lab.research.validation_v2 import (
    ValidationV2SourceBundle,
    ValidationV2SourceBundlePaths,
    load_validation_v2_source_bundle,
)
from market_structure_lab.research.validation_v2_models import (
    AccessAuditLedgerIdentityV2,
    CostAuthorityIdentityV2,
    SourcePublicationIdentityV2,
    ValidationProgrammeConfigV2,
    publication_json_bytes,
)


def _loader_inputs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[
    ValidationProgrammeConfigV2,
    ValidationV2SourceBundle,
    ValidationV2SourceBundlePaths,
    ValidationWorkBudget,
    ValidationWorkDemand,
]:
    from test_validation_v2_public_runner import public_programme_inputs

    config, sources, budget, demand = public_programme_inputs(
        None,
        tmp_path,
        monkeypatch,
    )
    controls = tmp_path / "loader-controls"
    controls.mkdir()
    coverage_path = controls / "coverage.json"
    split_path = controls / "split.json"
    boundary_path = controls / "boundary.json"
    coverage_path.write_bytes(sources.coverage.canonical_bytes)
    split_path.write_bytes(sources.split.canonical_bytes)
    boundary_path.write_bytes(sources.boundary.canonical_bytes)
    archive_root = tmp_path / "cost-inputs"
    paths = ValidationV2SourceBundlePaths(
        coverage_path=coverage_path,
        split_path=split_path,
        boundary_path=boundary_path,
        availability_root=tmp_path / "real-vs0001-chain" / "discovery",
        minute_publication_root=sources.source_publication.publication_root,
        minute_audit_root=sources.source_publication.audit_ledger_root,
        aggregate_publication_root=sources.aggregate_publication.publication_root,
        precision_authority_root=sources.precision_authority.publication_root,
        archive_manifest_path=archive_root / "archive-manifest.json",
        archive_publication_root=archive_root / "archive-publication",
        archive_cache_root=archive_root / "archive-cache",
        cost_authority_root=sources.cost_authority.publication_root,
    )
    return config, sources, paths, budget, demand


def test_loader_reconstructs_exact_bundle_from_publication_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config, original, paths, budget, demand = _loader_inputs(tmp_path, monkeypatch)

    loaded = load_validation_v2_source_bundle(
        config=config,
        paths=paths,
        budget=budget,
        admitted_demand=demand,
    )

    loaded.revalidate()
    assert loaded is not original
    assert loaded.coverage.coverage_identity == config.coverage_identity
    assert loaded.split.split_identity == config.split_identity
    assert loaded.source_publication.source_publication_identity == config.source_identity
    assert loaded.aggregate_publication.aggregate_identity == config.aggregate_identity
    assert loaded.precision_authority.precision_authority_identity == config.precision_identity
    assert loaded.cost_authority.cost_identity == config.cost_identity


def test_loader_rejects_declared_over_budget_before_artifact_open(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from market_structure_lab.research import validation_v2 as module

    config, _original, paths, budget, demand = _loader_inputs(tmp_path, monkeypatch)
    monkeypatch.setattr(
        module,
        "load_v2_boundary_publications",
        lambda **_kwargs: pytest.fail("artifact opened before budget admission"),
    )

    with pytest.raises(ValueError, match="source_rows"):
        load_validation_v2_source_bundle(
            config=config,
            paths=paths,
            budget=budget,
            admitted_demand=replace(demand, source_rows=budget.max_source_rows + 1),
        )


def test_loader_rejects_source_identity_before_partition_iteration(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from market_structure_lab.data import validation_source_v2 as source_module

    config, _original, paths, budget, demand = _loader_inputs(tmp_path, monkeypatch)
    wrong_config = replace(
        config,
        source_identity=SourcePublicationIdentityV2.from_payload({"test-only-wrong-source": True}),
    )
    monkeypatch.setattr(
        source_module,
        "_verify_minute_partition_tree",
        lambda *_args, **_kwargs: pytest.fail(
            "partition iteration occurred before source identity rejection"
        ),
    )

    with pytest.raises(ValueError, match="source publication differs from expected"):
        load_validation_v2_source_bundle(
            config=wrong_config,
            paths=paths,
            budget=budget,
            admitted_demand=demand,
        )


def test_loader_rejects_access_ledger_before_partition_iteration(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from market_structure_lab.data import validation_source_v2 as source_module

    config, _original, paths, budget, demand = _loader_inputs(tmp_path, monkeypatch)
    wrong_config = replace(
        config,
        access_ledger_identity=AccessAuditLedgerIdentityV2.from_payload(
            {"test-only-wrong-ledger": True}
        ),
    )
    monkeypatch.setattr(
        source_module,
        "_verify_minute_publication_audit",
        lambda *_args, **_kwargs: pytest.fail("source audit opened before access-ledger rejection"),
    )
    monkeypatch.setattr(
        source_module,
        "_verify_minute_partition_tree",
        lambda *_args, **_kwargs: pytest.fail(
            "partition iteration occurred before access-ledger rejection"
        ),
    )

    with pytest.raises(ValueError, match="access ledger identity differs"):
        load_validation_v2_source_bundle(
            config=wrong_config,
            paths=paths,
            budget=budget,
            admitted_demand=demand,
        )


def test_loader_rejects_physical_source_demand_before_partition_iteration(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from market_structure_lab.data import validation_source_v2 as source_module

    config, _original, paths, budget, demand = _loader_inputs(tmp_path, monkeypatch)
    monkeypatch.setattr(
        source_module,
        "_verify_minute_partition_tree",
        lambda *_args, **_kwargs: pytest.fail(
            "partition iteration occurred before physical demand rejection"
        ),
    )

    with pytest.raises(ValueError, match="row_count exceeds admitted maximum"):
        load_validation_v2_source_bundle(
            config=config,
            paths=paths,
            budget=budget,
            admitted_demand=replace(demand, source_rows=0),
        )


def test_loader_binds_cost_archive_parents_before_cache_open(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from market_structure_lab.data import binance_archive_v2 as archive_module

    config, _original, paths, budget, demand = _loader_inputs(tmp_path, monkeypatch)
    publication_path = paths.cost_authority_root / "publication.json"
    payload = json.loads(publication_path.read_bytes())
    payload["archive_publication_sha256"] = "f" * 64
    identity_payload = {
        key: value
        for key, value in payload.items()
        if key not in {"schema_version", "cost_identity"}
    }
    reauthored_identity = CostAuthorityIdentityV2.from_payload(identity_payload)
    payload["cost_identity"] = reauthored_identity.value
    publication_path.write_bytes(publication_json_bytes(payload))
    reauthored_config = replace(config, cost_identity=reauthored_identity)
    monkeypatch.setattr(
        archive_module,
        "_validate_acquisition_inventory",
        lambda *_args, **_kwargs: pytest.fail(
            "archive cache opened before parent identity rejection"
        ),
    )

    with pytest.raises(ValueError, match="archive acquisition publication differs"):
        load_validation_v2_source_bundle(
            config=reauthored_config,
            paths=paths,
            budget=budget,
            admitted_demand=demand,
        )


def test_loader_rejects_archive_work_over_budget_before_audit_or_cache(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from market_structure_lab.data import binance_archive_v2 as archive_module

    config, _original, paths, budget, demand = _loader_inputs(tmp_path, monkeypatch)
    archive_path = paths.archive_publication_root / "publication.json"
    archive_payload = json.loads(archive_path.read_bytes())
    archive_payload["compressed_bytes"] = budget.max_artifact_bytes + 1
    archive_identity_payload = {
        key: value for key, value in archive_payload.items() if key != "publication_sha256"
    }
    archive_payload["publication_sha256"] = hash_json(
        "phase5-binance-archive-acquisition-publication-v2",
        archive_identity_payload,
    )
    archive_path.write_bytes(publication_json_bytes(archive_payload))

    cost_path = paths.cost_authority_root / "publication.json"
    cost_payload = json.loads(cost_path.read_bytes())
    cost_payload["archive_publication_sha256"] = archive_payload["publication_sha256"]
    cost_identity_payload = {
        key: value
        for key, value in cost_payload.items()
        if key not in {"schema_version", "cost_identity"}
    }
    reauthored_cost_identity = CostAuthorityIdentityV2.from_payload(cost_identity_payload)
    cost_payload["cost_identity"] = reauthored_cost_identity.value
    cost_path.write_bytes(publication_json_bytes(cost_payload))
    reauthored_config = replace(config, cost_identity=reauthored_cost_identity)
    monkeypatch.setattr(
        archive_module,
        "_inspect_archive_audit_publication",
        lambda *_args, **_kwargs: pytest.fail("archive audit opened before budget rejection"),
    )
    monkeypatch.setattr(
        archive_module,
        "_validate_acquisition_inventory",
        lambda *_args, **_kwargs: pytest.fail("archive cache opened before budget rejection"),
    )

    with pytest.raises(ValueError, match="total bytes exceed admitted maximum"):
        load_validation_v2_source_bundle(
            config=reauthored_config,
            paths=paths,
            budget=budget,
            admitted_demand=demand,
        )


def test_loader_rejects_underdeclared_archive_object_totals_before_audit_or_cache(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from market_structure_lab.data import binance_archive_v2 as archive_module

    config, _original, paths, budget, demand = _loader_inputs(tmp_path, monkeypatch)
    archive_path = paths.archive_publication_root / "publication.json"
    archive_payload = json.loads(archive_path.read_bytes())
    local_sha256 = "1" * 64
    archive_payload["objects"] = [
        {
            "request_sha256": "2" * 64,
            "official_sha256": local_sha256,
            "local_sha256": local_sha256,
            "cache_object": f"sha256/{local_sha256[:2]}/{local_sha256}",
            "compressed_bytes": 1,
            "decompressed_bytes": 1,
            "row_count": 1,
        }
    ]
    archive_identity_payload = {
        key: value for key, value in archive_payload.items() if key != "publication_sha256"
    }
    archive_payload["publication_sha256"] = hash_json(
        "phase5-binance-archive-acquisition-publication-v2",
        archive_identity_payload,
    )
    archive_path.write_bytes(publication_json_bytes(archive_payload))

    cost_path = paths.cost_authority_root / "publication.json"
    cost_payload = json.loads(cost_path.read_bytes())
    cost_payload["archive_publication_sha256"] = archive_payload["publication_sha256"]
    cost_identity_payload = {
        key: value
        for key, value in cost_payload.items()
        if key not in {"schema_version", "cost_identity"}
    }
    reauthored_cost_identity = CostAuthorityIdentityV2.from_payload(cost_identity_payload)
    cost_payload["cost_identity"] = reauthored_cost_identity.value
    cost_path.write_bytes(publication_json_bytes(cost_payload))
    reauthored_config = replace(config, cost_identity=reauthored_cost_identity)
    monkeypatch.setattr(
        archive_module,
        "_inspect_archive_audit_publication",
        lambda *_args, **_kwargs: pytest.fail(
            "archive audit opened before aggregate object admission"
        ),
    )
    monkeypatch.setattr(
        archive_module,
        "_validate_acquisition_inventory",
        lambda *_args, **_kwargs: pytest.fail(
            "archive cache opened before aggregate object admission"
        ),
    )

    with pytest.raises(ValueError, match="aggregate object counts are inconsistent"):
        load_validation_v2_source_bundle(
            config=reauthored_config,
            paths=paths,
            budget=budget,
            admitted_demand=replace(
                demand,
                artifacts=1,
                artifact_bytes=2,
            ),
        )


def test_loader_reconciles_archive_artifact_count_against_admitted_demand(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from market_structure_lab.data import binance_archive_v2 as archive_module
    from market_structure_lab.research import validation_v2 as module

    config, original, paths, budget, demand = _loader_inputs(tmp_path, monkeypatch)
    manifest = archive_module.load_binance_archive_request_manifest_v2(
        publication_path=paths.archive_manifest_path,
        boundary=original.boundary,
        source_availability=original.availability,
    )
    acquisition = archive_module.load_binance_archive_acquisition_v2(
        publication_root=paths.archive_publication_root,
        cache_root=paths.archive_cache_root,
        boundary=original.boundary,
        manifest=manifest,
        manifest_path=paths.archive_manifest_path,
    )
    assert demand.artifacts < budget.max_artifacts
    object.__setattr__(acquisition, "object_count", demand.artifacts + 1)
    monkeypatch.setattr(
        module,
        "load_binance_archive_acquisition_v2",
        lambda **_kwargs: acquisition,
    )
    monkeypatch.setattr(
        module,
        "load_validation_cost_authority_v2",
        lambda **_kwargs: pytest.fail(
            "cost authority opened before artifact demand reconciliation"
        ),
    )

    with pytest.raises(ValueError, match="artifacts"):
        load_validation_v2_source_bundle(
            config=config,
            paths=paths,
            budget=budget,
            admitted_demand=demand,
        )
