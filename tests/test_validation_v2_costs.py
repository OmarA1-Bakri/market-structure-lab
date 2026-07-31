from __future__ import annotations

from dataclasses import fields, replace
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from market_structure_lab.research import validation_v2_costs as costs


SHA = "a" * 64


def _parents(
    tmp_path: Path, *, archive_status: str = "available", aggregate_status: str = "available"
):
    coverage = SimpleNamespace(coverage_identity=SimpleNamespace(value="SCV2-" + "0" * 64))
    split = SimpleNamespace(split_identity=SimpleNamespace(value="DSV2-" + "1" * 64))
    boundary = SimpleNamespace(
        boundary_sha256="2" * 64,
        canonical_bytes=b"boundary-original\n",
    )
    availability = SimpleNamespace(availability_sha256="3" * 64)
    minute = SimpleNamespace(
        source_publication_identity=SimpleNamespace(value="SRCV2-" + "4" * 64),
        canonical_bytes=b"minute-original\n",
    )
    aggregate = SimpleNamespace(
        status=SimpleNamespace(value=aggregate_status),
        aggregate_identity=SimpleNamespace(value="AGGV2-" + "5" * 64),
        canonical_bytes=b"aggregate-original\n",
    )
    manifest_path = tmp_path / "archive-manifest.json"
    manifest_path.write_bytes(b"manifest-original\n")
    requests = (
        SimpleNamespace(
            archive_kind="trades",
            object_path="data/spot/daily/trades/BTCUSDT/BTCUSDT-trades-2024-01-01.zip",
            checksum_path="data/spot/daily/trades/BTCUSDT/BTCUSDT-trades-2024-01-01.zip.CHECKSUM",
            request_sha256="6" * 64,
        ),
    )
    manifest = SimpleNamespace(
        manifest_sha256="7" * 64,
        boundary_sha256=boundary.boundary_sha256,
        source_availability_sha256=availability.availability_sha256,
        allowed_origin="https://data.binance.vision",
        requests=requests,
        canonical_bytes=b"manifest-original\n",
    )
    acquisition_path = tmp_path / "archive" / "publication.json"
    acquisition_path.parent.mkdir()
    acquisition_payload = {
        "objects": [
            {
                "request_sha256": requests[0].request_sha256,
                "official_sha256": "8" * 64,
                "local_sha256": "8" * 64,
                "cache_object": "sha256/88/object.zip",
                "compressed_bytes": 10,
                "decompressed_bytes": 20,
                "row_count": 2,
            }
        ]
    }
    acquisition_path.write_text(json.dumps(acquisition_payload), encoding="utf-8")
    acquisition = SimpleNamespace(
        status=archive_status,
        manifest_sha256=manifest.manifest_sha256,
        publication_sha256="9" * 64,
        publication_path=acquisition_path,
        audit_publication_sha256="b" * 64,
        canonical_bytes=acquisition_path.read_bytes(),
        object_count=1 if archive_status == "available" else 0,
    )
    return SimpleNamespace(
        coverage=coverage,
        split=split,
        boundary=boundary,
        availability=availability,
        minute=minute,
        aggregate=aggregate,
        manifest=manifest,
        manifest_path=manifest_path,
        acquisition=acquisition,
    )


def _patch_parent_verifiers(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(costs, "verify_development_read_boundary_v2", lambda *a, **k: a[0])
    monkeypatch.setattr(costs, "verify_validation_source_publication_v2", lambda *a, **k: a[0])
    monkeypatch.setattr(costs, "verify_validation_aggregate_publication_v2", lambda *a, **k: a[0])
    monkeypatch.setattr(costs, "verify_binance_archive_request_manifest_v2", lambda *a, **k: a[0])
    monkeypatch.setattr(costs, "verify_binance_archive_acquisition_v2", lambda *a, **k: a[0])


def _publish(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, **parent_overrides):
    _patch_parent_verifiers(monkeypatch)
    parents = _parents(tmp_path, **parent_overrides)
    authority = costs.publish_validation_cost_authority_v2(
        source_publication=parents.minute,
        aggregate_publication=parents.aggregate,
        coverage=parents.coverage,
        split=parents.split,
        boundary=parents.boundary,
        availability=parents.availability,
        archive_manifest=parents.manifest,
        archive_manifest_path=parents.manifest_path,
        archive_acquisition=parents.acquisition,
        output_root=tmp_path / "cost-authority",
    )
    return authority, parents


def test_required_dimensions_have_truthful_independent_semantics(tmp_path, monkeypatch) -> None:
    authority, _ = _publish(tmp_path, monkeypatch)
    evidence = {item.dimension.value: item for item in authority.dimensions}
    assert tuple(evidence) == costs.REQUIRED_COST_DIMENSIONS_V2
    assert evidence["fee"].status is costs.CostEvidenceStatusV2.UNAVAILABLE
    assert evidence["spread"].status is costs.CostEvidenceStatusV2.PROXY
    assert evidence["slippage"].status is costs.CostEvidenceStatusV2.PROXY
    assert evidence["funding"].status is costs.CostEvidenceStatusV2.NOT_APPLICABLE
    assert evidence["latency"].status is costs.CostEvidenceStatusV2.UNAVAILABLE
    assert evidence["fill"].status is costs.CostEvidenceStatusV2.UNAVAILABLE
    assert evidence["missed_fill"].status is costs.CostEvidenceStatusV2.UNAVAILABLE
    assert evidence["turnover"].status is costs.CostEvidenceStatusV2.OBSERVED
    assert evidence["capacity"].status is costs.CostEvidenceStatusV2.UNAVAILABLE
    assert all(item.value is None for item in authority.dimensions)


def test_trade_tape_proxies_are_explicit_and_never_promotion_grade(tmp_path, monkeypatch) -> None:
    authority, parents = _publish(tmp_path, monkeypatch)
    proxies = [
        item for item in authority.dimensions if item.status is costs.CostEvidenceStatusV2.PROXY
    ]
    assert {item.dimension.value for item in proxies} == {"spread", "slippage"}
    for proxy in proxies:
        assert proxy.formula
        assert proxy.units
        assert proxy.window
        assert proxy.exclusions
        assert proxy.archive_sha256 == ("8" * 64,)
        assert proxy.checksum_sha256 == ("8" * 64,)
        assert proxy.source_publication_identity == parents.minute.source_publication_identity.value
        assert proxy.boundary_sha256 == parents.boundary.boundary_sha256
        assert proxy.proxy_not_promotion_grade is True


def test_incomplete_costs_force_completed_inconclusive_and_never_edge_status(
    tmp_path, monkeypatch
) -> None:
    authority, _ = _publish(tmp_path, monkeypatch)
    assert authority.evaluation_status is costs.CostEvaluationStatusV2.COMPLETED
    assert authority.conclusion is costs.CostConclusionV2.INCONCLUSIVE
    assert set(authority.incomplete_promotion_grade_dimensions) == {
        "fee",
        "spread",
        "slippage",
        "latency",
        "fill",
        "missed_fill",
        "capacity",
    }
    serialized = authority.to_dict()
    assert "supported_development" not in json.dumps(serialized)
    assert "validated" not in json.dumps(serialized)
    assert "promoted" not in json.dumps(serialized)


def test_missing_required_archive_or_aggregate_forces_not_evaluated(tmp_path, monkeypatch) -> None:
    authority, _ = _publish(tmp_path, monkeypatch, archive_status="unavailable")
    assert authority.evaluation_status is costs.CostEvaluationStatusV2.NOT_EVALUATED
    assert authority.conclusion is costs.CostConclusionV2.INCONCLUSIVE
    assert authority.not_evaluated_reasons == ("archive_unavailable",)
    assert all(
        item.status is costs.CostEvidenceStatusV2.UNAVAILABLE
        for item in authority.dimensions
        if item.dimension is not costs.CostDimensionV2.FUNDING
    )


def test_spot_funding_not_applicable_requires_verified_spot_archive_identity(
    tmp_path, monkeypatch
) -> None:
    _patch_parent_verifiers(monkeypatch)
    parents = _parents(tmp_path)
    parents.manifest.requests[0].object_path = "data/futures/daily/trades/BTCUSDT/x.zip"
    with pytest.raises(ValueError, match="spot"):
        costs.publish_validation_cost_authority_v2(
            source_publication=parents.minute,
            aggregate_publication=parents.aggregate,
            coverage=parents.coverage,
            split=parents.split,
            boundary=parents.boundary,
            availability=parents.availability,
            archive_manifest=parents.manifest,
            archive_manifest_path=parents.manifest_path,
            archive_acquisition=parents.acquisition,
            output_root=tmp_path / "cost-authority",
        )


def test_direct_construction_copy_and_unregistered_reconstruction_are_rejected(
    tmp_path, monkeypatch
) -> None:
    authority, parents = _publish(tmp_path, monkeypatch)
    with pytest.raises(TypeError, match="publisher"):
        replace(authority)
    reconstructed = object.__new__(costs.VerifiedCostAuthorityV2)
    for definition in fields(authority):
        object.__setattr__(reconstructed, definition.name, getattr(authority, definition.name))
    with pytest.raises(ValueError, match="registered original"):
        costs.verify_validation_cost_authority_v2(
            reconstructed,
            source_publication=parents.minute,
            aggregate_publication=parents.aggregate,
            coverage=parents.coverage,
            split=parents.split,
            boundary=parents.boundary,
            availability=parents.availability,
            archive_manifest=parents.manifest,
            archive_manifest_path=parents.manifest_path,
            archive_acquisition=parents.acquisition,
        )


def test_every_consumption_revalidates_parent_and_original_bytes(tmp_path, monkeypatch) -> None:
    calls = {"archive": 0, "aggregate": 0}
    _patch_parent_verifiers(monkeypatch)
    parents = _parents(tmp_path)
    monkeypatch.setattr(
        costs,
        "verify_binance_archive_acquisition_v2",
        lambda *a, **k: calls.__setitem__("archive", calls["archive"] + 1) or a[0],
    )
    monkeypatch.setattr(
        costs,
        "verify_validation_aggregate_publication_v2",
        lambda *a, **k: calls.__setitem__("aggregate", calls["aggregate"] + 1) or a[0],
    )
    authority = costs.publish_validation_cost_authority_v2(
        source_publication=parents.minute,
        aggregate_publication=parents.aggregate,
        coverage=parents.coverage,
        split=parents.split,
        boundary=parents.boundary,
        availability=parents.availability,
        archive_manifest=parents.manifest,
        archive_manifest_path=parents.manifest_path,
        archive_acquisition=parents.acquisition,
        output_root=tmp_path / "cost-authority",
    )
    before = calls.copy()
    costs.verified_cost_authority_bytes_v2(authority)
    assert calls["archive"] == before["archive"] + 1
    assert calls["aggregate"] == before["aggregate"] + 1
    (tmp_path / "cost-authority" / "publication.json").write_bytes(b"{}\n")
    with pytest.raises(ValueError, match="original bytes"):
        costs.verified_cost_authority_bytes_v2(authority)


def test_existing_destination_and_symlink_are_no_clobber(tmp_path, monkeypatch) -> None:
    _patch_parent_verifiers(monkeypatch)
    parents = _parents(tmp_path)
    destination = tmp_path / "existing"
    destination.mkdir()
    marker = destination / "keep"
    marker.write_text("preserve", encoding="utf-8")
    with pytest.raises(FileExistsError):
        costs.publish_validation_cost_authority_v2(
            source_publication=parents.minute,
            aggregate_publication=parents.aggregate,
            coverage=parents.coverage,
            split=parents.split,
            boundary=parents.boundary,
            availability=parents.availability,
            archive_manifest=parents.manifest,
            archive_manifest_path=parents.manifest_path,
            archive_acquisition=parents.acquisition,
            output_root=destination,
        )
    assert marker.read_text(encoding="utf-8") == "preserve"
    link = tmp_path / "link"
    link.symlink_to(destination, target_is_directory=True)
    with pytest.raises((FileExistsError, RuntimeError)):
        costs.publish_validation_cost_authority_v2(
            source_publication=parents.minute,
            aggregate_publication=parents.aggregate,
            coverage=parents.coverage,
            split=parents.split,
            boundary=parents.boundary,
            availability=parents.availability,
            archive_manifest=parents.manifest,
            archive_manifest_path=parents.manifest_path,
            archive_acquisition=parents.acquisition,
            output_root=link,
        )


def test_dimension_substitution_and_coherent_self_rehash_do_not_replace_original(
    tmp_path, monkeypatch
) -> None:
    authority, _ = _publish(tmp_path, monkeypatch)
    original_dimensions = authority.dimensions
    swapped = list(original_dimensions)
    swapped[1], swapped[2] = swapped[2], swapped[1]
    object.__setattr__(authority, "dimensions", tuple(swapped))
    forged_identity = costs.CostAuthorityIdentityV2.from_payload(authority._identity_payload())
    object.__setattr__(authority, "cost_identity", forged_identity)
    forged_bytes = costs.publication_json_bytes(authority.to_dict())
    object.__setattr__(authority, "canonical_bytes", forged_bytes)
    (tmp_path / "cost-authority" / "publication.json").write_bytes(forged_bytes)
    (tmp_path / "cost-authority" / "_SUCCESS").write_text(
        f"{forged_identity.value}\n", encoding="ascii"
    )
    with pytest.raises(ValueError, match="original bytes|dimensions"):
        costs.verified_cost_authority_bytes_v2(authority)


def test_mixed_parent_capability_is_rejected(tmp_path, monkeypatch) -> None:
    authority, parents = _publish(tmp_path, monkeypatch)
    mixed_source = SimpleNamespace(
        source_publication_identity=parents.minute.source_publication_identity,
        canonical_bytes=parents.minute.canonical_bytes,
    )
    with pytest.raises(ValueError, match="parent capability"):
        costs.verify_validation_cost_authority_v2(
            authority,
            source_publication=mixed_source,
            aggregate_publication=parents.aggregate,
            coverage=parents.coverage,
            split=parents.split,
            boundary=parents.boundary,
            availability=parents.availability,
            archive_manifest=parents.manifest,
            archive_manifest_path=parents.manifest_path,
            archive_acquisition=parents.acquisition,
        )


def test_loader_requires_frozen_identity_and_exact_publisher_derivation(
    tmp_path, monkeypatch
) -> None:
    authority, parents = _publish(tmp_path, monkeypatch)
    loaded = costs.load_validation_cost_authority_v2(
        publication_root=tmp_path / "cost-authority",
        expected_cost_identity=authority.cost_identity,
        source_publication=parents.minute,
        aggregate_publication=parents.aggregate,
        coverage=parents.coverage,
        split=parents.split,
        boundary=parents.boundary,
        availability=parents.availability,
        archive_manifest=parents.manifest,
        archive_manifest_path=parents.manifest_path,
        archive_acquisition=parents.acquisition,
    )
    assert costs.verified_cost_authority_bytes_v2(loaded) == authority.canonical_bytes
    with pytest.raises(ValueError, match="expected publisher-derived identity"):
        costs.load_validation_cost_authority_v2(
            publication_root=tmp_path / "cost-authority",
            expected_cost_identity=costs.CostAuthorityIdentityV2("CSTV2-" + "f" * 64),
            source_publication=parents.minute,
            aggregate_publication=parents.aggregate,
            coverage=parents.coverage,
            split=parents.split,
            boundary=parents.boundary,
            availability=parents.availability,
            archive_manifest=parents.manifest,
            archive_manifest_path=parents.manifest_path,
            archive_acquisition=parents.acquisition,
        )
