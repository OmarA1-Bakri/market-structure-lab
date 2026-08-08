from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
import shutil
from types import SimpleNamespace
from typing import Any, cast

import pytest

from market_structure_lab.core.config import DatabaseSettings, MarketDataSettings
import copy
import hashlib
import json

from market_structure_lab.data.validation_source_v2 import (
    ScopedSourceStatusV2,
    discover_scoped_source_v2,
    finalize_scoped_source_candidate_evidence_v2,
    issue_postgresql_minute_source_capability_v2,
    publish_scoped_source_candidate_inputs_v2,
    reject_pg_restore_row_source_v2,
    verify_dump_toc_metadata_v2,
    verify_scoped_source_candidate_evidence_v2,
    verify_scoped_source_availability_v2,
)
from market_structure_lab.data.recovery import RecoveryCandle
from market_structure_lab.data.reconciliation import (
    ReconciliationClass,
    ReconciliationRecord,
    ReconciliationWorkUnit,
    SourceArtifactIdentity,
    TradingEnvelope,
    freeze_reconciliation_run,
    publish_work_unit,
    write_reconciliation_run,
)
from market_structure_lab.data.validation_rr_ledger_v2 import (
    iter_scoped_rr_ledger_rows_v2,
    verify_rr_ledger_inventory_v2,
)


from market_structure_lab.core.identity import hash_json
from market_structure_lab.research.validation_v2_models import (
    RawDumpIdentityV2,
    ReconciliationAuthorityV2,
    SourceCoverageEntryV2,
    SourceCoveragePublicationV2,
    publication_json_bytes,
)
from market_structure_lab.research.validation_v2_splits import (
    AccessOperationKindV2,
    BoundaryRequestV2,
    SplitPolicyV2,
    freeze_development_split_v2,
    issue_development_read_boundary_v2,
)


_LIVE_VIEW_DEFINITION = """
SELECT * FROM market_data.candle_reconciliation_promotions
ORDER BY promotion_id DESC;
SELECT * FROM market_data.candle_reconciliation_replacements replacement;
SELECT * FROM market_data.candle_reconciliation_coverage coverage;
SELECT * FROM market_data.candles candle
WHERE replacement.open_time = candle.open_time
AND coverage.start_time <= candle.open_time
AND candle.open_time < coverage.end_time;
"""


class _MetadataEngine:
    def __init__(self, metadata: dict[str, object]) -> None:
        self.metadata = metadata
        self.events: list[str] = []

    def connect(self):  # type: ignore[no-untyped-def]
        self.events.append("connect")
        return self

    def __enter__(self):  # type: ignore[no-untyped-def]
        return self

    def __exit__(self, *_exc: object) -> None:
        return None

    def execute(self, _statement):  # type: ignore[no-untyped-def]
        self.events.append("metadata_query")
        return self

    def mappings(self):  # type: ignore[no-untyped-def]
        return self

    def one(self) -> dict[str, object]:
        return dict(self.metadata)

    def dispose(self) -> None:
        self.events.append("dispose")


def _settings() -> MarketDataSettings:
    return MarketDataSettings(database=DatabaseSettings(password="fixture-secret"))


def _publish_reviewer(**kwargs):  # type: ignore[no-untyped-def]
    from market_structure_lab.data import validation_source_v2 as module

    return module._publish_independent_scoped_source_verifier_with_engine_v2(  # noqa: SLF001
        **kwargs,
        engine=_MetadataEngine(_production_metadata()),
    )


class _PathOwnedTreeClaim:
    def __init__(self, path: Path, move_hook=None) -> None:  # type: ignore[no-untyped-def]
        self.path = path
        self.identity = path.stat().st_ino
        self.move_hook = move_hook

    def __enter__(self):  # type: ignore[no-untyped-def]
        return self

    def __exit__(self, *_exc: object) -> None:
        return None

    @property
    def manifest(self) -> tuple[tuple[str, str | None], ...]:
        owned = self._locate_owned()
        if owned is None:
            return ()
        return tuple(
            (
                path.relative_to(owned).as_posix(),
                None if path.is_dir() else hashlib.sha256(path.read_bytes()).hexdigest(),
            )
            for path in sorted(owned.rglob("*"))
        )

    def move_to(self, destination: Path) -> None:
        if self.move_hook is not None:
            self.move_hook(self, destination)
        owned = self._locate_owned()
        if owned is None:
            raise RuntimeError("owned handle path was replaced")
        owned.rename(destination)
        self.path = destination

    def move_member_to(self, relative_path: str | Path, destination: Path) -> None:
        owned = self._locate_owned()
        if owned is None:
            raise RuntimeError("owned handle path was replaced")
        (owned / relative_path).rename(destination)

    def delete_exact(self) -> None:
        owned = self._locate_owned()
        if owned is not None:
            shutil.rmtree(owned)

    def _locate_owned(self) -> Path | None:
        for candidate in self.path.parent.iterdir():
            if candidate.is_dir() and candidate.stat().st_ino == self.identity:
                return candidate
        return None


@pytest.fixture
def issued_v2_publications():
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
    return coverage, split, issue_development_read_boundary_v2(coverage, split)


def test_dump_metadata_accepts_only_public_candles_and_never_row_restore() -> None:
    toc = b"42; 0 0 TABLE DATA public candles owner\n"
    metadata = verify_dump_toc_metadata_v2(toc)
    assert metadata.table == "public.candles"
    with pytest.raises(ValueError, match="public.candles"):
        verify_dump_toc_metadata_v2(b"42; 0 0 TABLE DATA market_data candles owner\n")
    with pytest.raises(PermissionError, match="metadata only"):
        reject_pg_restore_row_source_v2(("pg_restore", "--data-only", "callscore.dump"))


def test_discovery_truthfully_seals_unavailable_without_reading_rows(
    issued_v2_publications, tmp_path: Path
) -> None:
    _, _, boundary = issued_v2_publications
    candidates = tmp_path / "candidates"
    candidates.mkdir()
    (candidates / "whole-table.json").write_text(
        '{"schema_version":"phase5-scoped-source-candidate-v2",'
        '"source_kind":"postgres-restored-whole-table","read_only":true,'
        '"predicate_enforcement":"client-side-post-filter","boundary_sha256":"'
        + boundary.boundary_sha256
        + '","source_identity":"whole-table"}\n',
        encoding="utf-8",
    )
    result = discover_scoped_source_v2(
        boundary=boundary,
        candidate_root=candidates,
        audit_ledger_root=tmp_path / "audit",
        output_root=tmp_path / "publication",
    )
    assert result.status is ScopedSourceStatusV2.UNAVAILABLE
    assert result.rows_read == result.final_rows == result.final_access_records == 0
    assert result.candidates[0].rejection_reason == "whole_table_source_forbidden"
    assert (
        verify_scoped_source_availability_v2(
            result, boundary=boundary, publication_root=tmp_path / "publication"
        )
        == result
    )
    with pytest.raises(FrozenInstanceError):
        result.rows_read = 1  # type: ignore[misc]


def test_availability_capability_cannot_be_directly_constructed(
    issued_v2_publications, tmp_path: Path
) -> None:
    from market_structure_lab.data.validation_source_v2 import ScopedSourceAvailabilityV2

    _, _, boundary = issued_v2_publications
    with pytest.raises(TypeError, match="factory"):
        ScopedSourceAvailabilityV2.unsealed_for_test(boundary.boundary_sha256)


def test_availability_rejects_deleted_or_tampered_original_audit(
    issued_v2_publications, tmp_path: Path
) -> None:
    _, _, boundary = issued_v2_publications
    candidates = tmp_path / "candidates"
    candidates.mkdir()
    audit = tmp_path / "audit"
    output = tmp_path / "publication"
    availability = discover_scoped_source_v2(
        boundary=boundary,
        candidate_root=candidates,
        audit_ledger_root=audit,
        output_root=output,
    )
    (audit / "publication.json").write_text("{}\n", encoding="utf-8")

    with pytest.raises(ValueError, match="audit"):
        verify_scoped_source_availability_v2(
            availability, boundary=boundary, publication_root=output
        )


def test_copied_availability_loses_sealed_authority(issued_v2_publications, tmp_path: Path) -> None:
    _, _, boundary = issued_v2_publications
    candidates = tmp_path / "candidates"
    candidates.mkdir()
    output = tmp_path / "publication"
    availability = discover_scoped_source_v2(
        boundary=boundary,
        candidate_root=candidates,
        audit_ledger_root=tmp_path / "audit",
        output_root=output,
    )
    with pytest.raises(ValueError, match="original publication"):
        verify_scoped_source_availability_v2(
            copy.copy(availability), boundary=boundary, publication_root=output
        )


def _trusted_candidate_files(root: Path, boundary) -> tuple[Path, str]:
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
    original_path = root / "original.json"
    original_path.write_bytes(original_bytes)
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
    (root / "predicate.json").write_bytes(predicate_bytes)
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
    (root / "verifier.json").write_bytes(verifier_bytes)
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
    descriptor_path = root / "candidate.json"
    descriptor_path.write_bytes(publication_json_bytes(descriptor))
    return descriptor_path, hashlib.sha256(verifier_bytes).hexdigest()


def test_admission_requires_original_independent_predicate_evidence(
    issued_v2_publications, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from market_structure_lab.data import validation_source_v2 as module

    _, _, boundary = issued_v2_publications
    candidates = tmp_path / "candidates"
    candidates.mkdir()
    _, verifier_sha256 = _trusted_candidate_files(candidates, boundary)
    monkeypatch.setattr(module, "_TRUSTED_VERIFIER_EVIDENCE_SHA256", frozenset({verifier_sha256}))
    output = tmp_path / "publication"
    availability = discover_scoped_source_v2(
        boundary=boundary,
        candidate_root=candidates,
        audit_ledger_root=tmp_path / "audit",
        output_root=output,
    )
    assert availability.status is ScopedSourceStatusV2.AVAILABLE, availability.candidates

    (candidates / "predicate.json").write_bytes(
        (candidates / "predicate.json").read_bytes().replace(b"false", b"true", 1)
    )
    with pytest.raises(ValueError, match="candidate|original"):
        verify_scoped_source_availability_v2(
            availability, boundary=boundary, publication_root=output
        )


def test_self_attested_descriptor_is_immutably_unavailable(
    issued_v2_publications, tmp_path: Path
) -> None:
    _, _, boundary = issued_v2_publications
    candidates = tmp_path / "candidates"
    candidates.mkdir()
    self_attested = {
        "schema_version": "phase5-scoped-source-candidate-v2",
        "source_kind": "content-addressed-development-publication",
        "source_identity": "self-attested",
        "read_only": True,
        "predicate_enforcement": "partition-scope-before-open",
        "boundary_sha256": boundary.boundary_sha256,
        "verification": {
            "immutable": True,
            "predicate_verified_before_read": True,
            "verifier_identity": "caller",
            "original_manifest_sha256": "a" * 64,
        },
    }
    (candidates / "candidate.json").write_bytes(publication_json_bytes(self_attested))
    availability = discover_scoped_source_v2(
        boundary=boundary,
        candidate_root=candidates,
        audit_ledger_root=tmp_path / "audit",
        output_root=tmp_path / "publication",
    )
    assert availability.status is ScopedSourceStatusV2.UNAVAILABLE
    assert (
        availability.candidates[0].rejection_reason == "trusted_original_predicate_evidence_invalid"
    )


def test_candidate_evidence_producer_binds_verified_ancestry_without_self_authorising(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from market_structure_lab.data import validation_source_v2 as module

    coverage, split, boundary, ancestry = _candidate_ancestry_publications(tmp_path)
    inputs = tmp_path / "inputs"
    verifier = tmp_path / "reviewer"
    candidates = tmp_path / "candidates"

    publish_scoped_source_candidate_inputs_v2(
        coverage=coverage,
        split=split,
        boundary=boundary,
        raw_dump_path=ancestry[0],
        rr_promotion_receipt_path=ancestry[1],
        recovery_manifest_path=ancestry[2],
        reconciled_view_definition_path=ancestry[3],
        output_root=inputs,
    )
    assert {path.name for path in inputs.iterdir()} == {"original.json", "predicate.json"}
    original = json.loads((inputs / "original.json").read_bytes())
    predicate = json.loads((inputs / "predicate.json").read_bytes())
    assert original["source_readable_timeframes"] == ["1m"]
    assert original["derived_target_timeframes"] == ["1h", "4h"]
    assert predicate["source_readable_timeframes"] == ["1m"]
    assert predicate["derived_target_timeframes"] == ["1h", "4h"]
    assert "allowed_timeframes" not in original
    assert "allowed_timeframes" not in predicate
    dump_stat = ancestry[0].stat()
    assert original["source_ancestry"] == {
        "raw_dump_path": str(ancestry[0].resolve()),
        "raw_dump_sha256": hashlib.sha256(ancestry[0].read_bytes()).hexdigest(),
        "raw_dump_byte_count": dump_stat.st_size,
        "raw_dump_device": dump_stat.st_dev,
        "raw_dump_inode": dump_stat.st_ino,
        "raw_dump_mtime_ns": dump_stat.st_mtime_ns,
        "raw_dump_ctime_ns": dump_stat.st_ctime_ns,
        "pg_restore_list_sha256": coverage.raw_dump.pg_restore_list_sha256,
        "candle_table_toc_identity": coverage.raw_dump.candle_table_toc_identity,
        "source_mapping_version": coverage.raw_dump.source_mapping_version,
        "rr_promotion_receipt_path": str(ancestry[1].resolve()),
        "rr_promotion_receipt_sha256": hashlib.sha256(ancestry[1].read_bytes()).hexdigest(),
        "recovery_manifest_path": str(ancestry[2].resolve()),
        "recovery_manifest_sha256": hashlib.sha256(ancestry[2].read_bytes()).hexdigest(),
        "reconciled_view_definition_path": str(ancestry[3].resolve()),
        "reconciled_view_definition_sha256": hashlib.sha256(ancestry[3].read_bytes()).hexdigest(),
        "source_view": "market_data.candles_reconciled",
        "view_evidence_kind": "migration-definition-code-ancestry-not-live-state",
        "coverage_identity": coverage.coverage_identity.value,
        "raw_dump_identity_sha256": coverage.raw_dump.identity_sha256,
        "reconciliation_identity_sha256": coverage.reconciliation.identity_sha256,
    }
    assert original["use_policy"] == {
        "use_class": "private-quantitative-research",
        "technical_use_only": True,
        "redistribution_authorized": False,
        "commercial_use_authorized": False,
        "data_rights_status": "not-established-by-this-evidence",
    }
    _publish_reviewer(
        boundary=boundary,
        candidate_inputs_root=inputs,
        output_root=verifier,
    )
    published = finalize_scoped_source_candidate_evidence_v2(
        boundary=boundary,
        candidate_inputs_root=inputs,
        verifier_evidence_root=verifier,
        output_root=candidates,
    )
    reopened = verify_scoped_source_candidate_evidence_v2(
        coverage=coverage,
        split=split,
        boundary=boundary,
        publication_root=candidates,
    )

    assert reopened == published
    assert published.source_identity.startswith("callscore-rr000008-reconciled-view-v2:")
    assert published.verifier_evidence_sha256 not in frozenset()
    availability = discover_scoped_source_v2(
        boundary=boundary,
        candidate_root=candidates,
        audit_ledger_root=tmp_path / "audit",
        output_root=tmp_path / "availability",
    )
    assert availability.status is ScopedSourceStatusV2.UNAVAILABLE
    assert availability.candidates[0].rejection_reason == (
        "trusted_original_predicate_evidence_invalid"
    )
    monkeypatch.setattr(
        module,
        "_TRUSTED_VERIFIER_EVIDENCE_SHA256",
        frozenset({published.verifier_evidence_sha256}),
    )
    admitted = discover_scoped_source_v2(
        boundary=boundary,
        candidate_root=candidates,
        audit_ledger_root=tmp_path / "reviewed-audit",
        output_root=tmp_path / "reviewed-availability",
    )
    assert admitted.status is ScopedSourceStatusV2.AVAILABLE
    original_dump_bytes = ancestry[0].read_bytes()
    ancestry[0].write_bytes(original_dump_bytes[::-1])
    assert ancestry[0].stat().st_size == len(original_dump_bytes)
    with pytest.raises(ValueError, match="candidate trusted evidence changed"):
        verify_scoped_source_availability_v2(
            admitted,
            boundary=boundary,
            publication_root=tmp_path / "reviewed-availability",
        )


def test_candidate_evidence_verifier_rejects_tamper_and_producer_is_no_clobber(
    tmp_path: Path,
) -> None:
    coverage, split, boundary, ancestry = _candidate_ancestry_publications(tmp_path)
    inputs = tmp_path / "inputs"
    publish_scoped_source_candidate_inputs_v2(
        coverage=coverage,
        split=split,
        boundary=boundary,
        raw_dump_path=ancestry[0],
        rr_promotion_receipt_path=ancestry[1],
        recovery_manifest_path=ancestry[2],
        reconciled_view_definition_path=ancestry[3],
        output_root=inputs,
    )
    with pytest.raises(FileExistsError, match="stale|concurrent"):
        publish_scoped_source_candidate_inputs_v2(
            coverage=coverage,
            split=split,
            boundary=boundary,
            raw_dump_path=ancestry[0],
            rr_promotion_receipt_path=ancestry[1],
            recovery_manifest_path=ancestry[2],
            reconciled_view_definition_path=ancestry[3],
            output_root=inputs,
        )

    predicate_path = inputs / "predicate.json"
    predicate_path.write_bytes(predicate_path.read_bytes().replace(b"false", b"true", 1))
    with pytest.raises(ValueError, match="predicate|digest|canonical"):
        _publish_reviewer(
            boundary=boundary,
            candidate_inputs_root=inputs,
            output_root=tmp_path / "reviewer",
        )


def test_candidate_dump_exact_bytes_are_rechecked_at_every_trust_stage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from market_structure_lab.data import validation_source_v2 as module

    producer = _candidate_ancestry_publications(tmp_path / "producer")
    _replace_same_size(producer[3][0])
    with pytest.raises(ValueError, match="dump bytes differ"):
        _publish_candidate_inputs(producer, tmp_path / "producer" / "inputs")

    reviewer = _candidate_ancestry_publications(tmp_path / "reviewer")
    reviewer_inputs = tmp_path / "reviewer" / "inputs"
    _publish_candidate_inputs(reviewer, reviewer_inputs)
    _replace_same_size(reviewer[3][0])
    with pytest.raises(ValueError, match="dump original bytes changed"):
        _publish_reviewer(
            boundary=reviewer[2],
            candidate_inputs_root=reviewer_inputs,
            output_root=tmp_path / "reviewer" / "evidence",
        )

    finalizer = _candidate_ancestry_publications(tmp_path / "finalizer")
    finalizer_inputs = tmp_path / "finalizer" / "inputs"
    finalizer_verifier = tmp_path / "finalizer" / "review"
    _publish_candidate_inputs(finalizer, finalizer_inputs)
    _publish_reviewer(
        boundary=finalizer[2],
        candidate_inputs_root=finalizer_inputs,
        output_root=finalizer_verifier,
    )
    _replace_same_size(finalizer[3][0])
    with pytest.raises(ValueError, match="dump original bytes changed"):
        finalize_scoped_source_candidate_evidence_v2(
            boundary=finalizer[2],
            candidate_inputs_root=finalizer_inputs,
            verifier_evidence_root=finalizer_verifier,
            output_root=tmp_path / "finalizer" / "candidate",
        )

    admission = _candidate_ancestry_publications(tmp_path / "admission")
    admission_inputs = tmp_path / "admission" / "inputs"
    admission_verifier = tmp_path / "admission" / "review"
    admission_candidate = tmp_path / "admission" / "candidate"
    _publish_candidate_inputs(admission, admission_inputs)
    verifier_sha = _publish_reviewer(
        boundary=admission[2],
        candidate_inputs_root=admission_inputs,
        output_root=admission_verifier,
    )
    finalize_scoped_source_candidate_evidence_v2(
        boundary=admission[2],
        candidate_inputs_root=admission_inputs,
        verifier_evidence_root=admission_verifier,
        output_root=admission_candidate,
    )
    monkeypatch.setattr(module, "_TRUSTED_VERIFIER_EVIDENCE_SHA256", frozenset({verifier_sha}))
    _replace_same_size(admission[3][0])
    availability = discover_scoped_source_v2(
        boundary=admission[2],
        candidate_root=admission_candidate,
        audit_ledger_root=tmp_path / "admission" / "audit",
        output_root=tmp_path / "admission" / "availability",
    )
    assert availability.status is ScopedSourceStatusV2.UNAVAILABLE
    assert availability.candidates[0].rejection_reason == (
        "trusted_original_predicate_evidence_invalid"
    )


def test_candidate_ancestry_rejects_traversal_symlink_and_forged_verifier(
    tmp_path: Path,
) -> None:
    publications = _candidate_ancestry_publications(tmp_path / "paths")
    coverage, split, boundary, ancestry = publications
    traversal = ancestry[0].parent / "missing" / ".." / ancestry[0].name
    with pytest.raises(ValueError, match="traversal"):
        publish_scoped_source_candidate_inputs_v2(
            coverage=coverage,
            split=split,
            boundary=boundary,
            raw_dump_path=traversal,
            rr_promotion_receipt_path=ancestry[1],
            recovery_manifest_path=ancestry[2],
            reconciled_view_definition_path=ancestry[3],
            output_root=tmp_path / "paths" / "traversal-inputs",
        )

    dump_link = tmp_path / "paths" / "dump-link"
    dump_link.symlink_to(ancestry[0])
    with pytest.raises(RuntimeError, match="symlink|reparse"):
        publish_scoped_source_candidate_inputs_v2(
            coverage=coverage,
            split=split,
            boundary=boundary,
            raw_dump_path=dump_link,
            rr_promotion_receipt_path=ancestry[1],
            recovery_manifest_path=ancestry[2],
            reconciled_view_definition_path=ancestry[3],
            output_root=tmp_path / "paths" / "symlink-inputs",
        )

    forged = _candidate_ancestry_publications(tmp_path / "forged")
    inputs = tmp_path / "forged" / "inputs"
    reviewer = tmp_path / "forged" / "reviewer"
    _publish_candidate_inputs(forged, inputs)
    _publish_reviewer(
        boundary=forged[2],
        candidate_inputs_root=inputs,
        output_root=reviewer,
    )
    verifier_path = reviewer / "verifier.json"
    verifier_path.write_bytes(verifier_path.read_bytes().replace(b"true", b"false", 1))
    with pytest.raises(ValueError, match="verifier evidence"):
        finalize_scoped_source_candidate_evidence_v2(
            boundary=forged[2],
            candidate_inputs_root=inputs,
            verifier_evidence_root=reviewer,
            output_root=tmp_path / "forged" / "candidate",
        )


def test_candidate_evidence_rejects_source_and_derived_timeframe_conflation(
    tmp_path: Path,
) -> None:
    publications = _candidate_ancestry_publications(tmp_path)
    inputs = tmp_path / "inputs"
    _publish_candidate_inputs(publications, inputs)
    original_path = inputs / "original.json"
    original = json.loads(original_path.read_bytes())
    original.pop("manifest_sha256")
    original["source_readable_timeframes"] = ["1m", "1h", "4h"]
    original["derived_target_timeframes"] = []
    original["manifest_sha256"] = hash_json(
        "phase5-development-scoped-source-manifest-v3", original
    )
    original_path.write_bytes(publication_json_bytes(original))

    predicate_path = inputs / "predicate.json"
    predicate = json.loads(predicate_path.read_bytes())
    predicate.pop("evidence_sha256")
    predicate["source_readable_timeframes"] = ["1m", "1h", "4h"]
    predicate["derived_target_timeframes"] = []
    predicate["evidence_sha256"] = hash_json(
        "phase5-development-source-predicate-evidence-v3", predicate
    )
    predicate_path.write_bytes(publication_json_bytes(predicate))
    with pytest.raises(ValueError, match="manifest|predicate"):
        _publish_reviewer(
            boundary=publications[2],
            candidate_inputs_root=inputs,
            output_root=tmp_path / "reviewer",
        )


def _publish_candidate_inputs(publications, output_root: Path) -> None:  # type: ignore[no-untyped-def]
    coverage, split, boundary, ancestry = publications
    publish_scoped_source_candidate_inputs_v2(
        coverage=coverage,
        split=split,
        boundary=boundary,
        raw_dump_path=ancestry[0],
        rr_promotion_receipt_path=ancestry[1],
        recovery_manifest_path=ancestry[2],
        reconciled_view_definition_path=ancestry[3],
        output_root=output_root,
    )


def _replace_same_size(path: Path) -> None:
    original = path.read_bytes()
    replacement = bytes(byte ^ 0xFF for byte in original)
    path.write_bytes(replacement)
    assert path.stat().st_size == len(original)


def _candidate_ancestry_publications(tmp_path: Path):  # type: ignore[no-untyped-def]
    ancestry_root = tmp_path / "ancestry"
    ancestry_root.mkdir(parents=True)
    dump_path = ancestry_root / "callscore.dump"
    rr_path = ancestry_root / "rr-000008-receipt.json"
    recovery_path = ancestry_root / "recovery-manifest.json"
    view_path = ancestry_root / "0002_candle_reconciliation.sql"
    dump_path.write_bytes(b"fixture dump bytes")
    rr_path.write_bytes(
        publication_json_bytes(
            {
                "run_id": "RR-000008",
                "manifest_sha256": "1" * 64,
                "replacement_logical_sha256": "2" * 64,
                "canonical_logical_sha256": "3" * 64,
            }
        )
    )
    recovery_path.write_bytes(
        publication_json_bytes({"run_id": "RR-000008", "run_manifest_sha256": "1" * 64})
    )
    view_path.write_text(
        "CREATE OR REPLACE VIEW market_data.candles_reconciled AS\n" + _LIVE_VIEW_DEFINITION,
        encoding="utf-8",
    )
    coverage = SourceCoveragePublicationV2.freeze(
        raw_dump=RawDumpIdentityV2(
            hashlib.sha256(dump_path.read_bytes()).hexdigest(),
            dump_path.stat().st_size,
            "b" * 64,
            "public.candles:42",
            "v1",
        ),
        reconciliation=ReconciliationAuthorityV2(
            hashlib.sha256(rr_path.read_bytes()).hexdigest(),
            ("d" * 64,),
            ("e" * 64,),
            "rr-000008-promoted-only",
        ),
        compatibility_metadata_sha256=hashlib.sha256(recovery_path.read_bytes()).hexdigest(),
        entries=tuple(
            SourceCoverageEntryV2(
                symbol,
                datetime(2024, 1, 1, tzinfo=UTC),
                datetime(2025, 1, 1, tzinfo=UTC),
                ("1m", "1h", "4h"),
                False,
                True,
                "f" * 64,
            )
            for symbol in ("ADAUSDT", "BNBUSDT", "BTCUSDT", "SOLUSDT", "XRPUSDT")
        ),
    )
    split = freeze_development_split_v2(
        coverage=coverage,
        policy=SplitPolicyV2(6, 180, 1, 5, "public-salt", 24, 24, ("1m", "1h", "4h")),
    )
    boundary = issue_development_read_boundary_v2(coverage, split)
    return coverage, split, boundary, (dump_path, rr_path, recovery_path, view_path)


def _production_metadata() -> dict[str, object]:
    return {
        "promotion_count": 1,
        "run_id": "RR-000008",
        "manifest_sha256": "1" * 64,
        "replacement_logical_sha256": "2" * 64,
        "canonical_logical_sha256": "3" * 64,
        "view_columns": (
            "source_row_id",
            "symbol",
            "interval",
            "open_time",
            "open",
            "high",
            "low",
            "close",
            "volume",
            "quote_volume",
            "trades",
            "created_at",
            "origin",
            "source_name",
            "payload_checksum",
            "recovery_run_id",
            "reconciliation_run_id",
        ),
        "view_definition": _LIVE_VIEW_DEFINITION,
    }


def _admitted_v3_source(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    from market_structure_lab.data import validation_source_v2 as module

    coverage, split, boundary, ancestry = _candidate_ancestry_publications(tmp_path)
    inputs = tmp_path / "inputs"
    reviewer = tmp_path / "reviewer"
    candidate = tmp_path / "candidate"
    _publish_candidate_inputs((coverage, split, boundary, ancestry), inputs)
    verifier_sha = _publish_reviewer(
        boundary=boundary,
        candidate_inputs_root=inputs,
        output_root=reviewer,
    )
    finalize_scoped_source_candidate_evidence_v2(
        boundary=boundary,
        candidate_inputs_root=inputs,
        verifier_evidence_root=reviewer,
        output_root=candidate,
    )
    monkeypatch.setattr(module, "_TRUSTED_VERIFIER_EVIDENCE_SHA256", frozenset({verifier_sha}))
    availability = discover_scoped_source_v2(
        boundary=boundary,
        candidate_root=candidate,
        audit_ledger_root=tmp_path / "audit",
        output_root=tmp_path / "availability",
    )
    assert availability.status is ScopedSourceStatusV2.AVAILABLE
    return coverage, split, boundary, availability


def _patch_test_rr_origin(monkeypatch: pytest.MonkeyPatch, module: object) -> None:
    monkeypatch.setattr(module, "_origin_rr_ledger_inventory", lambda *_args, **_kwargs: object())


def test_postgresql_capability_preflights_before_bounded_reconciled_rows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from market_structure_lab.data import validation_source_v2 as module

    _, _, boundary, availability = _admitted_v3_source(tmp_path / "admitted", monkeypatch)
    _patch_test_rr_origin(monkeypatch, module)
    engine = _MetadataEngine(_production_metadata())
    row_events: list[tuple[str, object]] = []
    timestamp = boundary.allowed_intervals[0].start

    def fake_authenticated(implementation, *, request, progress=None):  # type: ignore[no-untyped-def]
        row_events.append(
            (
                "iterator_created",
                {"implementation": implementation, "request": request, "progress": progress},
            )
        )
        yield module.CanonicalMinuteRowV2(
            timestamp=timestamp,
            symbol=request.symbol,
            timeframe="1m",
            open=Decimal("10"),
            high=Decimal("11"),
            low=Decimal("9"),
            close=Decimal("10.5"),
            volume=Decimal("2"),
        )

    monkeypatch.setattr(module, "_iter_rr_authenticated_postgresql_rows", fake_authenticated)
    monkeypatch.setattr(module, "create_engine", lambda _url: engine)
    capability = issue_postgresql_minute_source_capability_v2(
        boundary=boundary,
        availability=availability,
        settings=_settings(),
        batch_size=17,
    )
    deep_revalidations: list[str] = []
    original_revalidate = module.verified_scoped_source_availability_bytes_v2

    def count_deep_revalidation(*args, **kwargs):  # type: ignore[no-untyped-def]
        deep_revalidations.append("availability")
        return original_revalidate(*args, **kwargs)

    monkeypatch.setattr(
        module,
        "verified_scoped_source_availability_bytes_v2",
        count_deep_revalidation,
    )
    request = BoundaryRequestV2(
        symbol=boundary.allowed_symbols[0],
        timeframe="1m",
        start=boundary.allowed_intervals[0].start,
        end=boundary.allowed_intervals[0].end,
        operation_kind=AccessOperationKindV2.ITERATOR,
        target_identity=capability.origin_sha256,
    )
    rows = tuple(
        module._iter_verified_minute_source_rows(  # noqa: SLF001
            capability,
            boundary=boundary,
            availability=availability,
            request=request,
            request_index=0,
        )
    )
    assert rows[0].timestamp == timestamp
    assert rows[0].open == Decimal("10")
    assert row_events[0][0] == "iterator_created"
    kwargs = row_events[0][1]
    assert isinstance(kwargs, dict)
    assert kwargs["request"] == request
    implementation = kwargs["implementation"]
    assert implementation.batch_size == 17
    assert implementation.mapping.qualified_table == "market_data.candles_reconciled"
    assert "candles_canonical" not in implementation.mapping.qualified_table
    assert engine.events[:2] == ["connect", "metadata_query"]
    assert deep_revalidations == []


def test_minute_stream_requires_strict_order_but_permits_authenticated_gaps(tmp_path: Path) -> None:
    from market_structure_lab.data import validation_source_v2 as module

    start = datetime(2024, 1, 1, tzinfo=UTC)
    request = BoundaryRequestV2(
        symbol="ADAUSDT",
        timeframe="1m",
        start=start,
        end=start + timedelta(minutes=3),
        operation_kind=AccessOperationKindV2.ITERATOR,
        target_identity="a" * 64,
    )

    def row(offset: int):  # type: ignore[no-untyped-def]
        return module.CanonicalMinuteRowV2(
            timestamp=start + timedelta(minutes=offset),
            symbol=request.symbol,
            timeframe="1m",
            open=Decimal("1"),
            high=Decimal("1"),
            low=Decimal("1"),
            close=Decimal("1"),
            volume=Decimal("1"),
        )

    common = {
        "request": request,
        "interval_index": 0,
        "starting_partition_count": 0,
        "starting_row_count": 0,
        "starting_byte_count": 0,
        "max_rows_per_partition": 10,
        "max_total_rows": 10,
        "max_total_bytes": 100_000,
        "max_partitions": 10,
        "source_identity": "source",
        "origin_proof_sha256": "b" * 64,
    }
    gap_partitions, gap_rows, _ = module._stream_minute_request(  # noqa: SLF001
        iter((row(0), row(2))),
        publication_stage=tmp_path / "gap",
        **common,
    )
    assert gap_rows == 2 and sum(item.row_count for item in gap_partitions) == 2
    with pytest.raises(ValueError, match="ordered and unique"):
        module._stream_minute_request(  # noqa: SLF001
            iter((row(1), row(0))),
            publication_stage=tmp_path / "reordered",
            **common,
        )
    partitions, row_count, _ = module._stream_minute_request(  # noqa: SLF001
        iter((row(0), row(1), row(2))),
        publication_stage=tmp_path / "complete",
        **common,
    )
    assert row_count == 3
    assert sum(item.row_count for item in partitions) == 3


def test_postgresql_capability_rejects_before_row_access_and_cannot_be_forged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from market_structure_lab.data import validation_source_v2 as module

    _, _, boundary, availability = _admitted_v3_source(tmp_path / "admitted", monkeypatch)
    _patch_test_rr_origin(monkeypatch, module)
    engine = _MetadataEngine(_production_metadata())
    row_calls: list[str] = []
    monkeypatch.setattr(
        module,
        "_iter_rr_authenticated_postgresql_rows",
        lambda *_args, **_kwargs: row_calls.append("row_iterator") or iter(()),
    )
    selected_engine = [engine]
    created_engines: list[_MetadataEngine] = []

    def fake_create_engine(_url):  # type: ignore[no-untyped-def]
        created_engines.append(selected_engine[0])
        return selected_engine[0]

    monkeypatch.setattr(module, "create_engine", fake_create_engine)

    with pytest.raises(ValueError, match="batch size"):
        issue_postgresql_minute_source_capability_v2(
            boundary=boundary, availability=availability, settings=_settings(), batch_size=0
        )
    assert created_engines == [] and engine.events == [] and row_calls == []
    with pytest.raises(TypeError, match="unexpected keyword argument"):
        injected_issuer = getattr(module, "issue_postgresql_minute_source_capability_v2")
        injected_issuer(
            boundary=boundary,
            availability=availability,
            settings=_settings(),
            engine=engine,
            batch_size=1,
        )
    assert created_engines == []

    empty_candidates = tmp_path / "empty" / "candidates"
    empty_candidates.mkdir(parents=True)
    unavailable = discover_scoped_source_v2(
        boundary=boundary,
        candidate_root=empty_candidates,
        audit_ledger_root=tmp_path / "empty" / "audit",
        output_root=tmp_path / "empty" / "availability",
    )
    with pytest.raises(ValueError, match="AVAILABLE|admitted"):
        issue_postgresql_minute_source_capability_v2(
            boundary=boundary, availability=unavailable, settings=_settings(), batch_size=1
        )
    assert created_engines == [] and engine.events == [] and row_calls == []

    wrong_promotion = _production_metadata()
    wrong_promotion["run_id"] = "RR-000007"
    wrong_engine = _MetadataEngine(wrong_promotion)
    selected_engine[0] = wrong_engine
    with pytest.raises(ValueError, match="promotion differs"):
        issue_postgresql_minute_source_capability_v2(
            boundary=boundary,
            availability=availability,
            settings=_settings(),
            batch_size=1,
        )
    assert row_calls == []

    wrong_view = _production_metadata()
    wrong_view["view_columns"] = tuple(wrong_view["view_columns"])[1:]  # type: ignore[arg-type]
    selected_engine[0] = _MetadataEngine(wrong_view)
    with pytest.raises(ValueError, match="view columns"):
        issue_postgresql_minute_source_capability_v2(
            boundary=boundary,
            availability=availability,
            settings=_settings(),
            batch_size=1,
        )
    assert row_calls == []

    extra_union = _production_metadata()
    extra_union["view_definition"] = str(extra_union["view_definition"]) + " UNION SELECT 1"
    selected_engine[0] = _MetadataEngine(extra_union)
    with pytest.raises(ValueError, match="allowlisted verifier evidence"):
        issue_postgresql_minute_source_capability_v2(
            boundary=boundary,
            availability=availability,
            settings=_settings(),
            batch_size=1,
        )
    assert row_calls == []

    with pytest.raises(TypeError, match="verifier factory"):
        module.VerifiedDevelopmentMinuteSourceV2(
            source_identity="forged",
            origin_kind="admitted-predicate-source-v2",
            origin_sha256="a" * 64,
            origin_proof_sha256="b" * 64,
            boundary_sha256=boundary.boundary_sha256,
            availability_sha256=availability.availability_sha256,
            canonical_bytes=b"{}",
        )


def test_postgresql_capability_rechecks_live_metadata_and_exact_predicate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from market_structure_lab.data import validation_source_v2 as module

    _, _, boundary, availability = _admitted_v3_source(tmp_path / "admitted", monkeypatch)
    _patch_test_rr_origin(monkeypatch, module)
    engine = _MetadataEngine(_production_metadata())
    row_calls: list[str] = []
    monkeypatch.setattr(
        module,
        "_iter_rr_authenticated_postgresql_rows",
        lambda *_args, **_kwargs: row_calls.append("row_iterator") or iter(()),
    )
    monkeypatch.setattr(module, "create_engine", lambda _url: engine)
    capability = issue_postgresql_minute_source_capability_v2(
        boundary=boundary,
        availability=availability,
        settings=_settings(),
        batch_size=1,
    )
    outside = BoundaryRequestV2(
        symbol=boundary.allowed_symbols[0],
        timeframe="1m",
        start=boundary.allowed_intervals[0].start,
        end=boundary.allowed_intervals[0].end.replace(year=2026),
        operation_kind=AccessOperationKindV2.ITERATOR,
        target_identity=capability.origin_sha256,
    )
    connects_before = len(engine.events)
    with pytest.raises(PermissionError, match="boundary"):
        module._iter_verified_minute_source_rows(  # noqa: SLF001
            capability,
            boundary=boundary,
            availability=availability,
            request=outside,
            request_index=0,
        )
    assert len(engine.events) == connects_before and row_calls == []

    request = BoundaryRequestV2(
        symbol=boundary.allowed_symbols[0],
        timeframe="1m",
        start=boundary.allowed_intervals[0].start,
        end=boundary.allowed_intervals[0].end,
        operation_kind=AccessOperationKindV2.ITERATOR,
        target_identity=capability.origin_sha256,
    )
    engine.metadata["manifest_sha256"] = "9" * 64
    with pytest.raises(ValueError, match="promotion differs|metadata changed"):
        tuple(
            module._iter_verified_minute_source_rows(  # noqa: SLF001
                capability,
                boundary=boundary,
                availability=availability,
                request=request,
                request_index=0,
            )
        )
    assert row_calls == []


def test_paired_source_and_audit_publication_roll_back_together(
    issued_v2_publications,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from market_structure_lab.data import validation_source_v2 as module

    _, _, boundary = issued_v2_publications
    candidates = tmp_path / "candidates"
    candidates.mkdir()
    audit = tmp_path / "audit"
    output = tmp_path / "publication"
    original_reserve = module._reserve_publication_directory  # noqa: SLF001

    def fail_second_publish(destination: Path) -> None:
        if Path(destination) == audit:
            raise OSError("fixture second publication failure")
        original_reserve(destination)

    monkeypatch.setattr(module, "_reserve_publication_directory", fail_second_publish)
    with pytest.raises(OSError, match="second publication"):
        discover_scoped_source_v2(
            boundary=boundary,
            candidate_root=candidates,
            audit_ledger_root=audit,
            output_root=output,
        )
    assert not audit.exists()
    assert not output.exists()


def test_paired_source_refuses_concurrent_empty_destination_directory(
    issued_v2_publications,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from market_structure_lab.data import validation_source_v2 as module

    _, _, boundary = issued_v2_publications
    candidates = tmp_path / "candidates"
    candidates.mkdir()
    audit = tmp_path / "audit"
    output = tmp_path / "publication"
    original_reserve = module._reserve_publication_directory  # noqa: SLF001

    def race(destination: Path) -> None:
        if Path(destination) == output:
            output.mkdir()
        original_reserve(destination)

    monkeypatch.setattr(module, "_reserve_publication_directory", race)
    with pytest.raises(FileExistsError):
        discover_scoped_source_v2(
            boundary=boundary,
            candidate_root=candidates,
            audit_ledger_root=audit,
            output_root=output,
        )
    assert output.is_dir()
    assert not tuple(output.iterdir())
    assert not audit.exists()


def test_windows_paired_source_publication_uses_durable_moves_and_rolls_back(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from market_structure_lab.data import validation_source_v2 as module

    first_stage = tmp_path / "first-stage"
    second_stage = tmp_path / "second-stage"
    first_stage.mkdir()
    second_stage.mkdir()
    (first_stage / "publication.json").write_text("{}\n", encoding="utf-8")
    (second_stage / "publication.json").write_text("{}\n", encoding="utf-8")
    first_destination = tmp_path / "first"
    second_destination = tmp_path / "second"
    calls = 0

    def move(_claim: _PathOwnedTreeClaim, _destination: Path) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("second durable move failed")

    monkeypatch.setattr(module, "_is_windows_platform", lambda: True)
    monkeypatch.setattr(
        module,
        "_claim_windows_owned_tree",
        lambda path: _PathOwnedTreeClaim(path, move),
    )

    with pytest.raises(OSError, match="second durable move failed"):
        module._commit_paired_directories(  # noqa: SLF001
            first_stage=first_stage,
            first_destination=first_destination,
            second_stage=second_stage,
            second_destination=second_destination,
        )

    assert not first_destination.exists()
    assert not second_destination.exists()


def test_windows_paired_source_rollback_preserves_concurrent_foreign_replacement(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from market_structure_lab.data import validation_source_v2 as module

    first_stage = tmp_path / "first-stage"
    second_stage = tmp_path / "second-stage"
    first_stage.mkdir()
    second_stage.mkdir()
    (first_stage / "publication.json").write_text("owned\n", encoding="utf-8")
    (second_stage / "publication.json").write_text("second\n", encoding="utf-8")
    first_destination = tmp_path / "first"
    second_destination = tmp_path / "second"
    calls = 0

    def move(_claim: _PathOwnedTreeClaim, _destination: Path) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            first_destination.rename(tmp_path / "owned-away")
            first_destination.mkdir()
            (first_destination / "foreign-sentinel").write_text(
                "preserve",
                encoding="utf-8",
            )
            raise OSError("second durable move failed after concurrent replacement")

    monkeypatch.setattr(module, "_is_windows_platform", lambda: True)

    monkeypatch.setattr(
        module,
        "_claim_windows_owned_tree",
        lambda path: _PathOwnedTreeClaim(path, move),
    )
    monkeypatch.setattr(
        module,
        "_remove_staged_tree",
        lambda _path: (_ for _ in ()).throw(
            AssertionError("Windows rollback must not recursively delete by pathname")
        ),
    )

    with pytest.raises(OSError, match="second durable move"):
        module._commit_paired_directories(  # noqa: SLF001
            first_stage=first_stage,
            first_destination=first_destination,
            second_stage=second_stage,
            second_destination=second_destination,
        )

    assert (first_destination / "foreign-sentinel").read_text(encoding="utf-8") == "preserve"
    assert not second_destination.exists()
    assert not first_stage.exists()
    assert not second_stage.exists()


def test_windows_rollback_restores_foreign_tree_swapped_after_identity_check(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from market_structure_lab.data import validation_source_v2 as module

    first_stage = tmp_path / "first-stage"
    second_stage = tmp_path / "second-stage"
    first_stage.mkdir()
    second_stage.mkdir()
    (first_stage / "publication.json").write_text("owned\n", encoding="utf-8")
    (second_stage / "publication.json").write_text("second\n", encoding="utf-8")
    first_destination = tmp_path / "first"
    second_destination = tmp_path / "second"
    calls = 0

    def move(_claim: _PathOwnedTreeClaim, _destination: Path) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("second durable move failed")
        if calls == 3:
            first_destination.rename(tmp_path / "owned-away")
            first_destination.mkdir()
            (first_destination / "foreign-sentinel").write_text(
                "preserve",
                encoding="utf-8",
            )

    monkeypatch.setattr(module, "_is_windows_platform", lambda: True)

    monkeypatch.setattr(
        module,
        "_claim_windows_owned_tree",
        lambda path: _PathOwnedTreeClaim(path, move),
    )
    monkeypatch.setattr(
        module,
        "_remove_staged_tree",
        lambda _path: (_ for _ in ()).throw(
            AssertionError("Windows rollback must not recursively delete by pathname")
        ),
    )

    with pytest.raises(OSError, match="second durable move"):
        module._commit_paired_directories(  # noqa: SLF001
            first_stage=first_stage,
            first_destination=first_destination,
            second_stage=second_stage,
            second_destination=second_destination,
        )

    assert (first_destination / "foreign-sentinel").read_text(encoding="utf-8") == "preserve"
    assert not first_stage.exists()


def test_windows_paired_source_publication_commits_both_staged_trees(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from market_structure_lab.data import validation_source_v2 as module

    first_stage = tmp_path / "first-stage"
    second_stage = tmp_path / "second-stage"
    first_stage.mkdir()
    second_stage.mkdir()
    (first_stage / "publication.json").write_text("first\n", encoding="utf-8")
    (second_stage / "publication.json").write_text("second\n", encoding="utf-8")
    first_destination = tmp_path / "first"
    second_destination = tmp_path / "second"
    moves: list[tuple[Path, Path]] = []

    def move(claim: _PathOwnedTreeClaim, destination: Path) -> None:
        moves.append((claim.path, destination))

    monkeypatch.setattr(module, "_is_windows_platform", lambda: True)
    monkeypatch.setattr(
        module,
        "_claim_windows_owned_tree",
        lambda path: _PathOwnedTreeClaim(path, move),
    )

    module._commit_paired_directories(  # noqa: SLF001
        first_stage=first_stage,
        first_destination=first_destination,
        second_stage=second_stage,
        second_destination=second_destination,
    )

    assert moves == [
        (first_stage, first_destination),
        (second_stage, second_destination),
    ]
    assert (first_destination / "publication.json").read_text(encoding="utf-8") == "first\n"
    assert (second_destination / "publication.json").read_text(encoding="utf-8") == "second\n"


def test_windows_availability_loader_rejects_uncommitted_root_before_read(
    issued_v2_publications,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from market_structure_lab.data import validation_source_v2 as module

    _, _, boundary = issued_v2_publications
    publication = tmp_path / "publication"
    publication.mkdir()
    (publication / "publication.json").write_text("{}\n", encoding="utf-8")

    monkeypatch.setattr(module, "_is_windows_platform", lambda: True)
    monkeypatch.setattr(
        module,
        "_read_json",
        lambda _path: (_ for _ in ()).throw(
            AssertionError("uncommitted publication payload must not be read")
        ),
    )

    with pytest.raises(ValueError, match="commit is missing or ambiguous"):
        module.load_scoped_source_availability_v2(
            publication_root=publication,
            boundary=boundary,
        )


def test_windows_validation_source_loader_checks_commit_before_tree_read(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from market_structure_lab.data import validation_source_v2 as module

    publication = tmp_path / "publication"
    audit = tmp_path / "audit"
    publication.mkdir()
    audit.mkdir()
    (publication / "publication.json").write_text("{}\n", encoding="utf-8")
    (audit / "publication.json").write_text("{}\n", encoding="utf-8")

    monkeypatch.setattr(module, "verify_development_read_boundary_v2", lambda *_args: None)
    monkeypatch.setattr(module, "verified_source_coverage_bytes", lambda *_args: b"coverage")
    monkeypatch.setattr(
        module,
        "verified_scoped_source_availability_bytes_v2",
        lambda *_args, **_kwargs: b"availability",
    )
    monkeypatch.setattr(
        module,
        "_derive_verified_minute_source_origin",
        lambda **_kwargs: None,
    )
    monkeypatch.setattr(
        module,
        "_require_windows_pair_commit",
        lambda _first, _second: (_ for _ in ()).throw(ValueError("paired commit is absent")),
    )
    monkeypatch.setattr(
        module,
        "read_bounded_regular",
        lambda *_args: (_ for _ in ()).throw(
            AssertionError("uncommitted publication tree must not be read")
        ),
    )

    with pytest.raises(ValueError, match="paired commit is absent"):
        module.load_validation_source_publication_v2(
            publication_root=publication,
            audit_ledger_root=audit,
            coverage=object(),  # type: ignore[arg-type]
            split=object(),  # type: ignore[arg-type]
            boundary=object(),  # type: ignore[arg-type]
            availability=object(),  # type: ignore[arg-type]
        )


def test_windows_pair_commit_scan_is_bounded_before_accumulation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from market_structure_lab.data import validation_source_v2 as module

    for index in range(3):
        (tmp_path / f"entry-{index}").write_text("x", encoding="utf-8")
    monkeypatch.setattr(module, "_MAX_WINDOWS_PAIR_PARENT_ENTRIES", 2)

    with pytest.raises(RuntimeError, match="parent scan exceeds its bound"):
        module._bounded_windows_pair_commit_candidates(tmp_path)  # noqa: SLF001


def test_windows_paired_source_refuses_existing_destination_without_path_deletion(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from market_structure_lab.data import validation_source_v2 as module

    first_stage = tmp_path / "first-stage"
    second_stage = tmp_path / "second-stage"
    first_stage.mkdir()
    second_stage.mkdir()
    (first_stage / "publication.json").write_text("first\n", encoding="utf-8")
    (second_stage / "publication.json").write_text("second\n", encoding="utf-8")
    first_destination = tmp_path / "first"
    first_destination.mkdir()
    (first_destination / "winner").write_text("preserve", encoding="utf-8")

    monkeypatch.setattr(module, "_is_windows_platform", lambda: True)
    monkeypatch.setattr(
        module,
        "_claim_windows_owned_tree",
        lambda path: _PathOwnedTreeClaim(path),
    )

    with pytest.raises(FileExistsError):
        module._commit_paired_directories(  # noqa: SLF001
            first_stage=first_stage,
            first_destination=first_destination,
            second_stage=second_stage,
            second_destination=tmp_path / "second",
        )

    assert (first_destination / "winner").read_text(encoding="utf-8") == "preserve"
    assert not first_stage.exists()
    assert not second_stage.exists()


def test_windows_paired_source_validates_all_incomplete_roots_before_deletion(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from market_structure_lab.data import validation_source_v2 as module

    first_stage = tmp_path / "first-stage"
    second_stage = tmp_path / "second-stage"
    first_stage.mkdir()
    second_stage.mkdir()
    (first_stage / "publication.json").write_text("first\n", encoding="utf-8")
    (second_stage / "publication.json").write_text("second\n", encoding="utf-8")
    first_destination = tmp_path / "first"
    second_destination = tmp_path / "second"
    first_destination.mkdir()
    second_destination.mkdir()
    (first_destination / "publication.json").write_text("first\n", encoding="utf-8")
    (second_destination / "foreign").write_text("preserve\n", encoding="utf-8")

    monkeypatch.setattr(module, "_is_windows_platform", lambda: True)
    monkeypatch.setattr(
        module,
        "_claim_windows_owned_tree",
        lambda path: _PathOwnedTreeClaim(path),
    )

    with pytest.raises(FileExistsError, match="foreign incomplete publication"):
        module._commit_paired_directories(  # noqa: SLF001
            first_stage=first_stage,
            first_destination=first_destination,
            second_stage=second_stage,
            second_destination=second_destination,
        )

    assert (first_destination / "publication.json").read_text(encoding="utf-8") == "first\n"
    assert (second_destination / "foreign").read_text(encoding="utf-8") == "preserve\n"


def test_windows_public_discovery_stage_swap_preserves_foreign_tree(
    issued_v2_publications,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from market_structure_lab.data import validation_source_v2 as module

    _, _, boundary = issued_v2_publications
    candidates = tmp_path / "candidates"
    candidates.mkdir()
    output = tmp_path / "publication"
    audit = tmp_path / "audit"
    calls = 0

    def move(_claim: _PathOwnedTreeClaim, _destination: Path) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            output.rename(tmp_path / "owned-away")
            output.mkdir()
            (output / "foreign-sentinel").write_text("preserve", encoding="utf-8")
            raise OSError("second publication failed after stage swap")

    monkeypatch.setattr(module, "_is_windows_platform", lambda: True)
    monkeypatch.setattr(
        module,
        "_claim_windows_owned_tree",
        lambda path: _PathOwnedTreeClaim(path, move),
    )

    with pytest.raises(OSError, match="stage swap"):
        discover_scoped_source_v2(
            boundary=boundary,
            candidate_root=candidates,
            audit_ledger_root=audit,
            output_root=output,
        )

    assert (output / "foreign-sentinel").read_text(encoding="utf-8") == "preserve"
    assert not audit.exists()
    assert not tuple(tmp_path.glob(".*.tmp"))


def _rr_authenticated_fixture(tmp_path: Path):  # type: ignore[no-untyped-def]
    start = datetime(2024, 1, 1, tzinfo=UTC)
    start_ms = int(start.timestamp() * 1000)
    candles = (
        RecoveryCandle(
            "BTCUSDT",
            "1m",
            start_ms,
            Decimal("0.123456789123456789"),
            Decimal("0.223456789123456789"),
            Decimal("0.023456789123456789"),
            Decimal("0.173456789123456789"),
            Decimal("2.123456789123456789"),
            Decimal("4.123456789123456789"),
            7,
        ),
        RecoveryCandle(
            "BTCUSDT",
            "1m",
            start_ms + 120_000,
            Decimal("10.000000000000000001"),
            Decimal("11.000000000000000001"),
            Decimal("9.000000000000000001"),
            Decimal("10.500000000000000001"),
            Decimal("3.000000000000000001"),
            Decimal("30.000000000000000001"),
            11,
        ),
    )
    unit = ReconciliationWorkUnit.create("BTCUSDT", "1m", start_ms, start_ms + 180_000)
    run = freeze_reconciliation_run(
        run_id="RR-000008",
        cutoff=datetime(2026, 7, 16, tzinfo=UTC),
        dump_sha256="a" * 64,
        source_row_count=3,
        mapping_version="mapping-v1",
        candidate_venue="binance",
        market_type="spot",
        source_revision="binance-public-data-v1",
        algorithm_version="row-reconciliation-v1",
        code_commit="f" * 40,
        uv_lock_sha256="b" * 64,
        envelopes=(TradingEnvelope("BTCUSDT", "1m", start_ms, start_ms + 180_000),),
        work_units=(unit,),
    )
    records = (
        ReconciliationRecord(
            "BTCUSDT",
            "1m",
            start_ms,
            ReconciliationClass.EXACT_MATCH,
            candles[0].row_checksum(),
            "d" * 64,
            (),
        ),
        ReconciliationRecord(
            "BTCUSDT",
            "1m",
            start_ms + 60_000,
            ReconciliationClass.SOURCE_UNAVAILABLE,
            None,
            None,
            (),
        ),
        ReconciliationRecord(
            "BTCUSDT",
            "1m",
            start_ms + 120_000,
            ReconciliationClass.BINANCE_FILL,
            None,
            candles[1].row_checksum(),
            (),
        ),
    )
    manifest = publish_work_unit(
        records,
        output_root=tmp_path,
        run=run,
        work_unit=unit,
        source_artifacts=(
            SourceArtifactIdentity(
                location="https://data.binance.vision/fixture.zip",
                payload_sha256="c" * 64,
                published_sha256="c" * 64,
                source_revision="binance-public-data-v1",
                retrieved_at="2026-07-16T00:00:00Z",
            ),
        ),
        max_rows_per_part=2,
    )
    write_reconciliation_run(run, tmp_path / "RR-000008.run.json")
    root = tmp_path / "run_id=RR-000008"
    manifest_path = (
        root / "symbol=BTCUSDT" / "year=2024" / "month=01" / "manifest.json"
    )
    inventory = verify_rr_ledger_inventory_v2(
        root,
        symbols=("BTCUSDT",),
        intervals=((start, start + timedelta(minutes=3)),),
        expected_work_unit_manifest_sha256=(
            hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
        ),
        expected_comparison_part_sha256=tuple(part.sha256 for part in manifest.parts),
    )
    return start, candles, inventory


def _exact_source_row(candle: RecoveryCandle, origin: str) -> dict[str, object]:
    return {
        "symbol": candle.symbol,
        "timeframe": candle.timeframe,
        "open_time_ms": candle.open_time_ms,
        "open": format(candle.open, "f"),
        "high": format(candle.high, "f"),
        "low": format(candle.low, "f"),
        "close": format(candle.close, "f"),
        "volume": format(candle.volume, "f"),
        "quote_volume": None if candle.quote_volume is None else format(candle.quote_volume, "f"),
        "trades": None if candle.trades is None else str(candle.trades),
        "origin": origin,
        "recovery_run_id": None,
        "reconciliation_run_id": "RR-000008",
    }


def test_rr_lockstep_preserves_exact_decimal_and_accounts_unavailable_gap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from market_structure_lab.data import validation_source_v2 as module

    start, candles, inventory = _rr_authenticated_fixture(tmp_path)
    source_rows = (
        _exact_source_row(candles[0], "dump_verified_match"),
        _exact_source_row(candles[1], "binance_fill"),
    )
    monkeypatch.setattr(
        module, "_iter_exact_postgresql_minute_rows", lambda *_args, **_kwargs: iter(source_rows)
    )
    implementation = SimpleNamespace(rr_inventory=inventory, batch_size=2)
    request = BoundaryRequestV2(
        symbol="BTCUSDT",
        timeframe="1m",
        start=start,
        end=start + timedelta(minutes=3),
        operation_kind=AccessOperationKindV2.ITERATOR,
        target_identity="d" * 64,
    )
    rows = tuple(
        module._iter_rr_authenticated_postgresql_rows(
            cast(Any, implementation), request=request
        )
    )
    assert tuple(row.timestamp for row in rows) == (start, start + timedelta(minutes=2))
    assert rows[0].open == Decimal("0.123456789123456789")
    assert rows[1].close == Decimal("10.500000000000000001")


def test_rr_ledger_rejects_part_substitution_before_rows(tmp_path: Path) -> None:
    start, _candles, inventory = _rr_authenticated_fixture(tmp_path)
    part = next(inventory.root.rglob("part-00000.parquet"))
    content = bytearray(part.read_bytes())
    content[-1] ^= 0xFF
    part.write_bytes(content)
    with pytest.raises(ValueError, match="part original bytes changed"):
        tuple(
            iter_scoped_rr_ledger_rows_v2(
                inventory,
                symbol="BTCUSDT",
                start=start,
                end=start + timedelta(minutes=3),
                batch_size=2,
            )
        )


def test_rr_inventory_reopens_scoped_rows_and_keeps_final_counters_zero(
    tmp_path: Path,
) -> None:
    start, _candles, inventory = _rr_authenticated_fixture(tmp_path)
    reopened = verify_rr_ledger_inventory_v2(
        inventory.root,
        symbols=("BTCUSDT",),
        intervals=((start, start + timedelta(minutes=3)),),
        expected_work_unit_manifest_sha256=(
            inventory.expected_work_unit_manifest_sha256
        ),
        expected_comparison_part_sha256=inventory.expected_comparison_part_sha256,
    )
    assert reopened.inventory_sha256 == inventory.inventory_sha256
    assert reopened.verifier_payload()["forbidden_part_opens"] == 0
    assert reopened.verifier_payload()["final_ledger_rows"] == 0


def test_rr_lockstep_rejects_missing_authenticated_source_row(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from market_structure_lab.data import validation_source_v2 as module

    start, candles, inventory = _rr_authenticated_fixture(tmp_path)
    monkeypatch.setattr(
        module,
        "_iter_exact_postgresql_minute_rows",
        lambda *_args, **_kwargs: iter((_exact_source_row(candles[0], "dump_verified_match"),)),
    )
    implementation = SimpleNamespace(rr_inventory=inventory, batch_size=2)
    request = BoundaryRequestV2(
        symbol="BTCUSDT",
        timeframe="1m",
        start=start,
        end=start + timedelta(minutes=3),
        operation_kind=AccessOperationKindV2.ITERATOR,
        target_identity="d" * 64,
    )
    with pytest.raises(ValueError, match="missing an RR-authenticated"):
        tuple(
            module._iter_rr_authenticated_postgresql_rows(
                cast(Any, implementation), request=request
            )
        )


def test_rr_inventory_rejects_alternate_coherent_run_substitution(
    tmp_path: Path,
) -> None:
    from market_structure_lab.data import validation_source_v2 as module

    _start, _candles, inventory = _rr_authenticated_fixture(tmp_path)
    with pytest.raises(ValueError, match="promotion receipt"):
        module._require_rr_promotion_run_binding(inventory, "e" * 64)  # noqa: SLF001


def test_exact_sql_executes_with_integer_predicate_and_no_timestamp_coercion() -> None:
    from market_structure_lab.data import validation_source_v2 as module

    class EmptyMappings:
        def fetchmany(self, _size: int) -> list[object]:
            return []

    class EmptyResult:
        def mappings(self) -> EmptyMappings:
            return EmptyMappings()

    class Connection:
        def __init__(self) -> None:
            self.sql = ""
            self.parameters: dict[str, object] = {}

        def __enter__(self):  # type: ignore[no-untyped-def]
            return self

        def __exit__(self, *_exc: object) -> None:
            return None

        def execution_options(self, **_kwargs):  # type: ignore[no-untyped-def]
            return self

        def execute(self, statement, parameters):  # type: ignore[no-untyped-def]
            self.sql = str(statement)
            self.parameters = dict(parameters)
            return EmptyResult()

    connection = Connection()
    engine = SimpleNamespace(connect=lambda: connection)
    start = datetime(2024, 1, 1, tzinfo=UTC)
    request = BoundaryRequestV2(
        symbol="BTCUSDT",
        timeframe="1m",
        start=start,
        end=start + timedelta(minutes=1),
        operation_kind=AccessOperationKindV2.ITERATOR,
        target_identity="a" * 64,
    )
    implementation = SimpleNamespace(engine=engine, batch_size=17)
    assert (
        tuple(
            module._iter_exact_postgresql_minute_rows(
                cast(Any, implementation), request=request
            )
        )
        == ()
    )
    assert "source.open_time >= :start_ms" in connection.sql
    assert "source.open_time < :end_ms" in connection.sql
    assert "extract(epoch" not in connection.sql.lower()
    assert "timestamptz" not in connection.sql.lower()
    assert connection.parameters["start_ms"] == int(start.timestamp() * 1000)
    assert isinstance(connection.parameters["start_ms"], int)


def test_rr_lockstep_accounts_prefetched_database_batch_before_mismatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from market_structure_lab.data import validation_source_v2 as module

    start, candles, inventory = _rr_authenticated_fixture(tmp_path)
    wrong = _exact_source_row(candles[1], "binance_fill")

    def prefetched(_implementation, *, request, on_batch=None):  # type: ignore[no-untyped-def]
        assert request.start == start
        if on_batch is not None:
            on_batch(2)
        yield wrong

    monkeypatch.setattr(module, "_iter_exact_postgresql_minute_rows", prefetched)
    progress: list[dict[str, int]] = []
    implementation = SimpleNamespace(rr_inventory=inventory, batch_size=2)
    request = BoundaryRequestV2(
        symbol="BTCUSDT",
        timeframe="1m",
        start=start,
        end=start + timedelta(minutes=3),
        operation_kind=AccessOperationKindV2.ITERATOR,
        target_identity="d" * 64,
    )
    with pytest.raises(ValueError, match="extra or missing"):
        tuple(
            module._iter_rr_authenticated_postgresql_rows(
                cast(Any, implementation),
                request=request,
                progress=lambda counts: progress.append(dict(counts)),
            )
        )
    assert progress[-1]["database"] == 2
    assert progress[-1]["ledger"] == 2
