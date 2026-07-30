from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
import hashlib
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
    origin_kind = "admitted-predicate-source-v2"

    def __init__(self, boundary, availability, origin_sha256: str) -> None:
        self.source_identity = availability.admitted_source_identity
        self.origin_sha256 = origin_sha256
        self.boundary_sha256 = boundary.boundary_sha256
        self.availability_sha256 = availability.availability_sha256
        self.calls = 0
        self.row_mutator = lambda row: row

    def iter_rows(self, request):
        self.calls += 1
        row = CanonicalMinuteRowV2(
            timestamp=request.start,
            symbol=request.symbol,
            timeframe=request.timeframe,
            open=Decimal("1.2300"),
            high=Decimal("1.2500"),
            low=Decimal("1.200"),
            close=Decimal("1.240"),
            volume=Decimal("10.5000"),
        )
        yield self.row_mutator(row)


def _publish(source_chain, tmp_path: Path, reader=None, suffix: str = ""):
    coverage, split, boundary, availability, origin = source_chain
    reader = reader or FixtureReader(boundary, availability, origin)
    result = publish_validation_source_v2(
        coverage=coverage,
        split=split,
        boundary=boundary,
        availability=availability,
        reader=reader,
        publication_root=tmp_path / f"source{suffix}",
        audit_ledger_root=tmp_path / f"audit{suffix}",
        max_rows_per_partition=1,
        max_total_rows=1_000,
        max_total_bytes=2_000_000,
        max_partitions=1_000,
    )
    return result, reader


def _tree_bytes(root: Path) -> dict[str, bytes]:
    return {
        relative: (root / relative).read_bytes()
        for relative in bounded_regular_files(root, maximum=10_000)
    }


def test_available_publication_is_bounded_canonical_and_byte_identical(
    source_chain, tmp_path: Path
) -> None:
    coverage, split, boundary, availability, _ = source_chain
    first, reader = _publish(source_chain, tmp_path, suffix="-one")
    second, _ = _publish(source_chain, tmp_path, suffix="-two")

    assert first.status is ScopedSourceStatusV2.AVAILABLE
    assert first.row_count == len(boundary.allowed_symbols) * len(boundary.allowed_intervals)
    assert reader.calls == first.row_count
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
        reader=Bomb(),
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
    reader = FixtureReader(boundary, availability, origin)
    if attack == "final_row":
        reader.row_mutator = lambda row: CanonicalMinuteRowV2(
            timestamp=boundary.forbidden_temporal_intervals[0].start,
            symbol=row.symbol,
            timeframe=row.timeframe,
            open=row.open,
            high=row.high,
            low=row.low,
            close=row.close,
            volume=row.volume,
        )
    elif attack == "mixed_parent":
        reader.boundary_sha256 = "0" * 64
    else:
        reader.iter_rows = lambda request: (_ for _ in ()).throw(
            RuntimeError("fixture interrupted source")
        )

    with pytest.raises((PermissionError, RuntimeError, ValueError)):
        _publish(source_chain, tmp_path, reader=reader)
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
