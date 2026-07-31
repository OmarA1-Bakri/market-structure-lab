from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
import hashlib
import copy
import json
from pathlib import Path

import pytest

from market_structure_lab.core.artifact_io import bounded_regular_files
from market_structure_lab.core.identity import hash_json
from market_structure_lab.data.validation_source_v2 import (
    CanonicalMinuteRowV2,
    ScopedSourceStatusV2,
    discover_scoped_source_v2,
    load_validation_source_publication_v2,
    publish_validation_source_v2,
    verify_validation_source_publication_v2,
)
from market_structure_lab.research.validation_v2_models import (
    RawDumpIdentityV2,
    ReconciliationAuthorityV2,
    SourceCoverageEntryV2,
    SourceCoveragePublicationV2,
    SourcePublicationIdentityV2,
    publication_json_bytes,
)
from market_structure_lab.research.validation_v2_splits import (
    SplitPolicyV2,
    freeze_development_split_v2,
    issue_development_read_boundary_v2,
)


@pytest.fixture
def source_chain(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from market_structure_lab.data import validation_source_v2 as module

    coverage = SourceCoveragePublicationV2.freeze(
        raw_dump=RawDumpIdentityV2("a" * 64, 1, "b" * 64, "public.candles:42", "v1"),
        reconciliation=ReconciliationAuthorityV2(
            "c" * 64, ("d" * 64,), ("e" * 64,), "rr-000008-promoted-only"
        ),
        compatibility_metadata_sha256="f" * 64,
        entries=tuple(
            SourceCoverageEntryV2(
                symbol,
                datetime(2024, 1, 1, tzinfo=UTC),
                datetime(2025, 1, 1, tzinfo=UTC),
                ("1m", "1h", "4h"),
                False,
                True,
                str(index) * 64,
            )
            for index, symbol in enumerate(
                ("ADAUSDT", "BNBUSDT", "DOGEUSDT", "SOLUSDT", "XRPUSDT"), 1
            )
        ),
    )
    split = freeze_development_split_v2(
        coverage=coverage,
        policy=SplitPolicyV2(6, 180, 1, 5, "public-salt", 24, 24, ("1m", "1h", "4h")),
    )
    boundary = issue_development_read_boundary_v2(coverage, split)
    candidates = tmp_path / "candidates"
    candidates.mkdir()
    source_identity = "content-addressed-development-source-fixture"
    scope = {
        "boundary_sha256": boundary.boundary_sha256,
        "allowed_symbols": list(boundary.allowed_symbols),
        "allowed_timeframes": list(boundary.allowed_timeframes),
        "allowed_intervals": [
            {
                "start": item.start.isoformat(timespec="seconds").replace("+00:00", "Z"),
                "end": item.end.isoformat(timespec="seconds").replace("+00:00", "Z"),
            }
            for item in boundary.allowed_intervals
        ],
    }
    original_payload = {
        "source_identity": source_identity,
        **scope,
        "read_only": True,
        "predicate_enforcement": "partition-scope-before-open",
    }
    original = {
        "schema_version": "phase5-development-scoped-source-manifest-v2",
        **original_payload,
        "manifest_sha256": hash_json(
            "phase5-development-scoped-source-manifest-v2", original_payload
        ),
    }
    original_bytes = publication_json_bytes(original)
    (candidates / "original.json").write_bytes(original_bytes)
    predicate_payload = {
        "source_identity": source_identity,
        **scope,
        "predicate_stage": "before-file-open-or-query",
        "client_post_filter": False,
        "unbounded_scan": False,
    }
    predicate = {
        "schema_version": "phase5-development-source-predicate-evidence-v2",
        **predicate_payload,
        "evidence_sha256": hash_json(
            "phase5-development-source-predicate-evidence-v2", predicate_payload
        ),
    }
    predicate_bytes = publication_json_bytes(predicate)
    (candidates / "predicate.json").write_bytes(predicate_bytes)
    verifier_payload = {
        "source_identity": source_identity,
        "original_manifest_sha256": hashlib.sha256(original_bytes).hexdigest(),
        "predicate_evidence_sha256": hashlib.sha256(predicate_bytes).hexdigest(),
        "boundary_sha256": boundary.boundary_sha256,
        "verifier_kind": "independent-original-byte-verifier",
        "immutable": True,
    }
    verifier = {
        "schema_version": "phase5-development-source-verifier-evidence-v2",
        **verifier_payload,
        "evidence_sha256": hash_json(
            "phase5-development-source-verifier-evidence-v2", verifier_payload
        ),
    }
    verifier_bytes = publication_json_bytes(verifier)
    (candidates / "verifier.json").write_bytes(verifier_bytes)
    descriptor = {
        "schema_version": "phase5-scoped-source-candidate-v2",
        "source_kind": "content-addressed-development-publication",
        "source_identity": source_identity,
        "original_manifest_path": "original.json",
        "original_manifest_sha256": hashlib.sha256(original_bytes).hexdigest(),
        "verifier_evidence_path": "verifier.json",
        "verifier_evidence_sha256": hashlib.sha256(verifier_bytes).hexdigest(),
        "predicate_evidence_path": "predicate.json",
        "predicate_evidence_sha256": hashlib.sha256(predicate_bytes).hexdigest(),
    }
    descriptor_bytes = publication_json_bytes(descriptor)
    (candidates / "candidate.json").write_bytes(descriptor_bytes)
    monkeypatch.setattr(
        module,
        "_TRUSTED_VERIFIER_EVIDENCE_SHA256",
        frozenset({hashlib.sha256(verifier_bytes).hexdigest()}),
    )
    availability = discover_scoped_source_v2(
        boundary=boundary,
        candidate_root=candidates,
        audit_ledger_root=tmp_path / "discovery-audit",
        output_root=tmp_path / "discovery",
    )
    assert availability.status is ScopedSourceStatusV2.AVAILABLE
    return (
        coverage,
        split,
        boundary,
        availability,
        hashlib.sha256(descriptor_bytes).hexdigest(),
    )


class FixtureReader:
    pass


def _capability(
    source_chain,
    *,
    row_mutator=lambda row: row,
    fail_request_index: int | None = None,
):
    from market_structure_lab.data import validation_source_v2 as module

    _, _, boundary, availability, _ = source_chain
    rows = tuple(
        (
            row_mutator(
                CanonicalMinuteRowV2(
                    timestamp=interval.start,
                    symbol=symbol,
                    timeframe="1m",
                    open=Decimal("1.2300"),
                    high=Decimal("1.2500"),
                    low=Decimal("1.200"),
                    close=Decimal("1.240"),
                    volume=Decimal("10.5000"),
                )
            ),
        )
        for symbol in boundary.allowed_symbols
        for interval in boundary.allowed_intervals
    )
    return module._issue_test_minute_source_capability_v2(  # noqa: SLF001
        boundary=boundary,
        availability=availability,
        request_rows=rows,
        fail_request_index=fail_request_index,
    )


def _publish(source_chain, tmp_path: Path, source_capability=None, suffix: str = ""):
    coverage, split, boundary, availability, _ = source_chain
    source_capability = source_capability or _capability(source_chain)
    result = publish_validation_source_v2(
        coverage=coverage,
        split=split,
        boundary=boundary,
        availability=availability,
        source_capability=source_capability,
        publication_root=tmp_path / f"source{suffix}",
        audit_ledger_root=tmp_path / f"audit{suffix}",
        max_rows_per_partition=1,
        max_total_rows=1_000,
        max_total_bytes=2_000_000,
        max_partitions=1_000,
    )
    return result, source_capability


def _tree_bytes(root: Path) -> dict[str, bytes]:
    return {
        relative: (root / relative).read_bytes()
        for relative in bounded_regular_files(root, maximum=10_000)
    }


def test_available_publication_is_bounded_canonical_and_byte_identical(
    source_chain, tmp_path: Path
) -> None:
    coverage, split, boundary, availability, _ = source_chain
    first, capability = _publish(source_chain, tmp_path, suffix="-one")
    second, _ = _publish(source_chain, tmp_path, suffix="-two")

    assert first.status is ScopedSourceStatusV2.AVAILABLE
    assert first.row_count == len(boundary.allowed_symbols) * len(boundary.allowed_intervals)
    assert not hasattr(capability, "iter_rows")
    assert first.source_publication_identity == second.source_publication_identity
    assert _tree_bytes(tmp_path / "source-one") == _tree_bytes(tmp_path / "source-two")
    assert _tree_bytes(tmp_path / "audit-one") == _tree_bytes(tmp_path / "audit-two")
    assert b'"open":"1.23"' in next(
        value
        for path, value in _tree_bytes(tmp_path / "source-one").items()
        if path.endswith(".jsonl")
    )
    assert (
        verify_validation_source_publication_v2(
            first,
            coverage=coverage,
            split=split,
            boundary=boundary,
            availability=availability,
        )
        == first
    )
    loaded = load_validation_source_publication_v2(
        publication_root=tmp_path / "source-one",
        audit_ledger_root=tmp_path / "audit-one",
        coverage=coverage,
        split=split,
        boundary=boundary,
        availability=availability,
    )
    assert loaded == first


def test_unavailable_publication_never_touches_reader(
    source_chain, tmp_path: Path
) -> None:
    coverage, split, boundary, _, _ = source_chain
    empty = tmp_path / "empty"
    empty.mkdir()
    unavailable = discover_scoped_source_v2(
        boundary=boundary,
        candidate_root=empty,
        audit_ledger_root=tmp_path / "unavailable-discovery-audit",
        output_root=tmp_path / "unavailable-discovery",
    )

    class Bomb:
        def __getattribute__(self, name):
            raise AssertionError(f"reader touched: {name}")

    result = publish_validation_source_v2(
        coverage=coverage,
        split=split,
        boundary=boundary,
        availability=unavailable,
        source_capability=Bomb(),
        publication_root=tmp_path / "source",
        audit_ledger_root=tmp_path / "audit",
        max_rows_per_partition=1,
        max_total_rows=1,
        max_total_bytes=1_000,
        max_partitions=1,
    )
    assert result.status is ScopedSourceStatusV2.UNAVAILABLE
    assert result.row_count == result.final_rows == result.final_access_records == 0
    assert not (tmp_path / "source" / "partitions").exists()


@pytest.mark.parametrize("attack", ("final_row", "mixed_parent", "partial_failure"))
def test_publication_rejects_scope_parent_and_partial_attacks(
    attack: str, source_chain, tmp_path: Path
) -> None:
    coverage, split, boundary, availability, origin = source_chain
    if attack == "final_row":
        capability = _capability(
            source_chain,
            row_mutator=lambda row: CanonicalMinuteRowV2(
                timestamp=boundary.forbidden_temporal_intervals[0].start,
                symbol=row.symbol,
                timeframe=row.timeframe,
                open=row.open,
                high=row.high,
                low=row.low,
                close=row.close,
                volume=row.volume,
            ),
        )
    elif attack == "mixed_parent":
        original = _capability(source_chain)
        capability = object.__new__(type(original))
        for name in (
            "source_identity",
            "origin_kind",
            "origin_sha256",
            "origin_proof_sha256",
            "boundary_sha256",
            "availability_sha256",
        ):
            object.__setattr__(capability, name, getattr(original, name))
        object.__setattr__(capability, "boundary_sha256", "0" * 64)
    else:
        capability = _capability(
            source_chain,
            fail_request_index=1,
        )

    with pytest.raises((PermissionError, RuntimeError, ValueError)):
        _publish(source_chain, tmp_path, source_capability=capability)
    assert not (tmp_path / "source").exists()
    assert not (tmp_path / "audit").exists()


def test_duck_typed_reader_imposter_and_copied_capability_are_rejected_pre_read(
    source_chain, tmp_path: Path
) -> None:
    _, _, boundary, availability, origin = source_chain

    class Imposter:
        source_identity = availability.admitted_source_identity
        origin_kind = "admitted-predicate-source-v2"
        origin_sha256 = origin
        origin_proof_sha256 = "a" * 64
        boundary_sha256 = boundary.boundary_sha256
        availability_sha256 = availability.availability_sha256
        calls = 0

        def iter_rows(self, request):
            self.calls += 1
            raise AssertionError("imposter reached read")

    imposter = Imposter()
    with pytest.raises((TypeError, ValueError), match="capability|factory|original"):
        _publish(source_chain, tmp_path, source_capability=imposter)
    assert imposter.calls == 0
    original = _capability(source_chain)
    with pytest.raises((TypeError, ValueError), match="capability|original"):
        _publish(
            source_chain,
            tmp_path,
            source_capability=copy.copy(original),
            suffix="-copy",
        )


def test_capability_reopens_original_source_evidence_before_read(
    source_chain, tmp_path: Path
) -> None:
    _, _, _, availability, _ = source_chain
    capability = _capability(source_chain)
    descriptor = Path(availability.candidates[0].descriptor_path)
    predicate = descriptor.parent / "predicate.json"
    predicate.write_bytes(predicate.read_bytes().replace(b"false", b"true", 1))

    with pytest.raises(ValueError, match="candidate|source|original"):
        _publish(source_chain, tmp_path, source_capability=capability)
    assert not (tmp_path / "source").exists()
    assert not (tmp_path / "audit").exists()


def test_verifier_rejects_mutation_and_symlink_substitution(
    source_chain, tmp_path: Path
) -> None:
    coverage, split, boundary, availability, _ = source_chain
    publication, _ = _publish(source_chain, tmp_path)
    partition = next((tmp_path / "source" / "partitions").rglob("*.jsonl"))
    original = partition.read_bytes()
    partition.write_bytes(original + b"{}\n")
    with pytest.raises((RuntimeError, ValueError), match="partition|bytes|artifact"):
        verify_validation_source_publication_v2(
            publication,
            coverage=coverage,
            split=split,
            boundary=boundary,
            availability=availability,
        )
    partition.unlink()
    partition.symlink_to(tmp_path / "source" / "publication.json")
    with pytest.raises((RuntimeError, ValueError)):
        load_validation_source_publication_v2(
            publication_root=tmp_path / "source",
            audit_ledger_root=tmp_path / "audit",
            coverage=coverage,
            split=split,
            boundary=boundary,
            availability=availability,
        )


def test_publication_refuses_concurrent_empty_destination(
    source_chain, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from market_structure_lab.data import validation_source_v2 as module

    output = tmp_path / "source"
    original_reserve = module._reserve_publication_directory  # noqa: SLF001

    def race(destination: Path) -> None:
        if Path(destination) == output:
            output.mkdir()
        original_reserve(destination)

    monkeypatch.setattr(module, "_reserve_publication_directory", race)
    with pytest.raises(FileExistsError):
        _publish(source_chain, tmp_path)
    assert output.is_dir() and not tuple(output.iterdir())
    assert not (tmp_path / "audit").exists()


def _rehash_source_manifest(path: Path, payload: dict[str, object]) -> None:
    identity_payload = {
        key: value
        for key, value in payload.items()
        if key not in {"schema_version", "source_publication_identity"}
    }
    payload["source_publication_identity"] = SourcePublicationIdentityV2.from_payload(
        identity_payload
    ).value
    path.write_bytes(publication_json_bytes(payload))
    (path.parent / "_SUCCESS").write_text(
        f"{payload['source_publication_identity']}\n", encoding="ascii"
    )


def test_loader_rejects_self_hashed_scope_and_aggregate_only_audit_attacks(
    source_chain, tmp_path: Path
) -> None:
    from market_structure_lab.data import validation_source_v2 as module

    coverage, split, boundary, availability, _ = source_chain
    for attack in ("forbidden_symbol", "moved_interval", "aggregate_only_audit"):
        publication, _ = _publish(source_chain, tmp_path, suffix=f"-{attack}")
        root = publication.publication_root
        audit_root = publication.audit_ledger_root
        manifest_path = root / "publication.json"
        manifest = json.loads(manifest_path.read_bytes())
        if attack == "forbidden_symbol":
            manifest["partitions"][0]["symbol"] = boundary.forbidden_asset_symbols[0]
        elif attack == "moved_interval":
            forbidden = boundary.forbidden_temporal_intervals[0]
            manifest["partitions"][0]["interval_start"] = (
                forbidden.start.isoformat(timespec="seconds").replace("+00:00", "Z")
            )
            manifest["partitions"][0]["interval_end"] = (
                forbidden.end.isoformat(timespec="seconds").replace("+00:00", "Z")
            )
        else:
            prior = None
            records = sorted((audit_root / "records").glob("*.json"))
            changed = False
            for record_path in records:
                record = json.loads(record_path.read_bytes())
                if record["phase"] == "completion" and not changed:
                    record["partition_set_sha256"] = "0" * 64
                    changed = True
                record["prior_record_sha256"] = prior
                record_payload = {
                    key: value
                    for key, value in record.items()
                    if key != "record_sha256"
                }
                record["record_sha256"] = hash_json(
                    module._MINUTE_PUBLICATION_AUDIT_RECORD_DOMAIN,  # noqa: SLF001
                    record_payload,
                )
                prior = record["record_sha256"]
                record_path.write_bytes(publication_json_bytes(record))
            audit_path = audit_root / "publication.json"
            audit = json.loads(audit_path.read_bytes())
            audit["terminal_record_sha256"] = prior
            audit_payload = {
                key: value
                for key, value in audit.items()
                if key != "audit_publication_sha256"
            }
            audit["audit_publication_sha256"] = hash_json(
                module._MINUTE_PUBLICATION_AUDIT_DOMAIN,  # noqa: SLF001
                audit_payload,
            )
            audit_path.write_bytes(publication_json_bytes(audit))
            manifest["audit_publication_sha256"] = audit[
                "audit_publication_sha256"
            ]
        _rehash_source_manifest(manifest_path, manifest)

        with pytest.raises(ValueError, match="scope|partition|audit|source"):
            load_validation_source_publication_v2(
                publication_root=root,
                audit_ledger_root=audit_root,
                coverage=coverage,
                split=split,
                boundary=boundary,
                availability=availability,
            )
