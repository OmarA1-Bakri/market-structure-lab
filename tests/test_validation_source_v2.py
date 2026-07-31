from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import UTC, datetime
from pathlib import Path
import shutil

import pytest
import copy
import hashlib

from market_structure_lab.data.validation_source_v2 import (
    ScopedSourceStatusV2,
    discover_scoped_source_v2,
    reject_pg_restore_row_source_v2,
    verify_dump_toc_metadata_v2,
    verify_scoped_source_availability_v2,
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
    SplitPolicyV2,
    freeze_development_split_v2,
    issue_development_read_boundary_v2,
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

    def move_to(self, destination: Path) -> None:
        if self.move_hook is not None:
            self.move_hook(self, destination)
        owned = self._locate_owned()
        if owned is None:
            raise RuntimeError("owned handle path was replaced")
        owned.rename(destination)
        self.path = destination

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
