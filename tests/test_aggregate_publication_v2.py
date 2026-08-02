from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
import copy
import gc
import hashlib
import json
import os
from pathlib import Path
from typing import BinaryIO, Callable
import weakref

import pytest

from market_structure_lab.core.artifact_io import bounded_regular_files
from market_structure_lab.core.identity import hash_json
from market_structure_lab.data.aggregate_publication_v2 import (
    AggregatePublicationBudgetV2,
    AggregatePublicationV2,
    AggregateSeriesKeyV2,
    VerifiedAggregateSeriesV2,
    issue_aggregate_series_key_v2,
    iter_verified_aggregate_rows_v2,
    load_validation_aggregate_publication_v2,
    open_verified_aggregate_series_v2,
    publish_validation_aggregates_v2,
    verify_verified_aggregate_series_v2,
    verify_validation_aggregate_publication_v2,
)
from market_structure_lab.data.validation_source_v2 import (
    CanonicalMinuteRowV2,
    ScopedSourceStatusV2,
    discover_scoped_source_v2,
    publish_validation_source_v2,
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


class _ReplaceOnEof:
    def __init__(self, handle: BinaryIO, replace_entry: Callable[[], None]) -> None:
        self._handle = handle
        self._replace_entry = replace_entry
        self._replaced = False

    def __enter__(self) -> _ReplaceOnEof:
        return self

    def __exit__(self, *_args: object) -> None:
        self._handle.close()

    def read(self, size: int = -1) -> bytes:
        chunk = self._handle.read(size)
        self._replace_after_eof(chunk)
        return chunk

    def readline(self, size: int = -1) -> bytes:
        line = self._handle.readline(size)
        self._replace_after_eof(line)
        return line

    def _replace_after_eof(self, chunk: bytes) -> None:
        if not chunk and not self._replaced:
            self._replaced = True
            self._replace_entry()


def _tree_bytes(root: Path) -> dict[str, bytes]:
    return {
        relative: (root / relative).read_bytes()
        for relative in bounded_regular_files(root, maximum=10_000)
    }


def _coherent_alternative_partition(original: bytes) -> bytes:
    encoded: list[bytes] = []
    for line in original.splitlines():
        payload = json.loads(line)
        assert isinstance(payload, dict)
        volume = payload["volume"]
        assert isinstance(volume, str) and volume[-1].isdigit()
        payload["volume"] = volume[:-1] + ("1" if volume[-1] != "1" else "2")
        identity_payload = dict(payload)
        identity_payload.pop("row_sha256")
        payload["row_sha256"] = hash_json(
            "phase5-validation-aggregate-row-v2",
            identity_payload,
        )
        encoded.append(
            (
                json.dumps(
                    payload,
                    ensure_ascii=False,
                    separators=(",", ":"),
                    sort_keys=True,
                )
                + "\n"
            ).encode("utf-8")
        )
    alternative = b"".join(encoded)
    assert len(alternative) == len(original)
    assert alternative != original
    return alternative


@pytest.fixture
def v2_chain(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from market_structure_lab.data import validation_source_v2 as source_module

    coverage = SourceCoveragePublicationV2.freeze(
        raw_dump=RawDumpIdentityV2("a" * 64, 1, "b" * 64, "public.candles:42", "mapping-v2"),
        reconciliation=ReconciliationAuthorityV2(
            "c" * 64, ("d" * 64,), ("e" * 64,), "rr-000008-promoted-only"
        ),
        compatibility_metadata_sha256="f" * 64,
        entries=tuple(
            SourceCoverageEntryV2(
                symbol,
                datetime(2025, 1, 1, tzinfo=UTC),
                datetime(2025, 1, 7, tzinfo=UTC),
                ("1m", "1h", "4h"),
                False,
                True,
                str(index) * 64,
            )
            for index, symbol in enumerate(("ADAUSDT", "SOLUSDT"), 1)
        ),
    )
    split = freeze_development_split_v2(
        coverage=coverage,
        policy=SplitPolicyV2(6, 6, 1, 2, "public-salt", 1, 1, ("1m", "1h", "4h")),
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
        source_module,
        "_TRUSTED_VERIFIER_EVIDENCE_SHA256",
        frozenset({hashlib.sha256(verifier_bytes).hexdigest()}),
    )
    availability = discover_scoped_source_v2(
        boundary=boundary,
        candidate_root=candidates,
        audit_ledger_root=tmp_path / "discovery-audit",
        output_root=tmp_path / "discovery",
    )
    return coverage, split, boundary, availability


def _minute_rows(boundary, *, gap: bool = False):
    requests = []
    for symbol in boundary.allowed_symbols:
        for interval in boundary.allowed_intervals:
            rows = []
            for offset in range(720 if gap else 240):
                if gap and 240 <= offset < 480:
                    continue
                value = Decimal("100.000") + Decimal(offset) / Decimal("1000")
                rows.append(
                    CanonicalMinuteRowV2(
                        timestamp=interval.start + timedelta(minutes=offset),
                        symbol=symbol,
                        timeframe="1m",
                        open=value,
                        high=value + Decimal("0.020"),
                        low=value - Decimal("0.010"),
                        close=value + Decimal("0.010"),
                        volume=Decimal("1.2500"),
                    )
                )
            requests.append(tuple(rows))
    return tuple(requests)


def _publish_minute(v2_chain, tmp_path: Path, *, rows=None, suffix: str = ""):
    from market_structure_lab.data import validation_source_v2 as source_module

    coverage, split, boundary, availability = v2_chain
    capability = source_module._issue_test_minute_source_capability_v2(  # noqa: SLF001
        boundary=boundary,
        availability=availability,
        request_rows=rows or _minute_rows(boundary),
        fail_request_index=None,
    )
    publication = publish_validation_source_v2(
        coverage=coverage,
        split=split,
        boundary=boundary,
        availability=availability,
        source_capability=capability,
        publication_root=tmp_path / f"minute{suffix}",
        audit_ledger_root=tmp_path / f"minute-audit{suffix}",
        max_rows_per_partition=73,
        max_total_rows=100_000,
        max_total_bytes=100_000_000,
        max_partitions=10_000,
    )
    return publication


def _budget(**overrides: int) -> AggregatePublicationBudgetV2:
    values = {
        "max_source_rows": 100_000,
        "max_source_bytes": 100_000_000,
        "max_parent_partitions": 10_000,
        "max_source_rows_per_chunk": 240,
        "max_members": 1_000,
        "max_aggregate_rows": 20_000,
        "max_rows_per_partition": 31,
        "max_output_bytes": 100_000_000,
        "max_output_files": 10_000,
    }
    values.update(overrides)
    return AggregatePublicationBudgetV2(**values)


def _publish_aggregate(v2_chain, minute, root: Path, *, budget=None):
    coverage, split, boundary, availability = v2_chain
    return publish_validation_aggregates_v2(
        minute_publication=minute,
        coverage=coverage,
        split=split,
        boundary=boundary,
        availability=availability,
        output_root=root,
        budget=budget or _budget(),
    )


def test_complete_decimal_aggregate_bundle_is_exact_and_byte_identical(
    v2_chain, tmp_path: Path
) -> None:
    minute_one = _publish_minute(v2_chain, tmp_path, suffix="-one")
    minute_two = _publish_minute(v2_chain, tmp_path, suffix="-two")
    first = _publish_aggregate(v2_chain, minute_one, tmp_path / "aggregate-one")
    second = _publish_aggregate(v2_chain, minute_two, tmp_path / "aggregate-two")
    _, _, boundary, _ = v2_chain

    assert isinstance(first, AggregatePublicationV2)
    assert first.status is ScopedSourceStatusV2.AVAILABLE
    assert first.aggregate_identity == second.aggregate_identity
    assert first.target_timeframes == ("1h", "4h")
    assert len(first.members) == len(boundary.allowed_symbols) * len(boundary.allowed_intervals) * 2
    assert first.final_scope_attempts == first.final_rows == first.final_access_records == 0
    assert _tree_bytes(tmp_path / "aggregate-one") == _tree_bytes(tmp_path / "aggregate-two")

    rows = tuple(iter_verified_aggregate_rows_v2(first, budget=_budget()))
    one_hour = next(row for row in rows if row.target_timeframe == "1h")
    four_hour = next(row for row in rows if row.target_timeframe == "4h")
    assert one_hour.source_row_count == 60
    assert one_hour.open == Decimal("100")
    assert one_hour.high == Decimal("100.079")
    assert one_hour.low == Decimal("99.99")
    assert one_hour.close == Decimal("100.069")
    assert one_hour.volume == Decimal("75")
    assert four_hour.source_row_count == 240
    assert four_hour.volume == Decimal("300")
    assert b'"open":"100"' in next(
        content
        for path, content in _tree_bytes(tmp_path / "aggregate-one").items()
        if path.endswith(".jsonl")
    )


def test_gap_starts_new_segments_without_cross_gap_aggregation(v2_chain, tmp_path: Path) -> None:
    minute = _publish_minute(v2_chain, tmp_path, rows=_minute_rows(v2_chain[2], gap=True))
    publication = _publish_aggregate(v2_chain, minute, tmp_path / "aggregate")

    rows = tuple(iter_verified_aggregate_rows_v2(publication, budget=_budget()))
    assert {row.segment_id for row in rows if row.target_timeframe == "1h"} == {0, 1}
    assert all(row.source_row_count in {60, 240} for row in rows)
    assert all(row.timestamp.minute == 0 and row.timestamp.second == 0 for row in rows)


def test_partial_terminal_bar_and_overlapping_parent_partition_reject(
    v2_chain, tmp_path: Path
) -> None:
    rows = list(_minute_rows(v2_chain[2]))
    rows[0] = rows[0][:-1]
    minute = _publish_minute(v2_chain, tmp_path, rows=tuple(rows))
    with pytest.raises(ValueError, match="partial|complete|aligned"):
        _publish_aggregate(v2_chain, minute, tmp_path / "partial")

    valid = _publish_minute(v2_chain, tmp_path, suffix="-valid")
    copied = copy.copy(valid)
    with pytest.raises((TypeError, ValueError), match="verified|original|publication"):
        _publish_aggregate(v2_chain, copied, tmp_path / "copied")


def test_preflight_budget_rejects_before_parent_row_iteration(
    v2_chain, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import market_structure_lab.data.aggregate_publication_v2 as module

    minute = _publish_minute(v2_chain, tmp_path)
    monkeypatch.setattr(
        module,
        "iter_bounded_regular_lines",
        lambda *_args, **_kwargs: pytest.fail("over-budget publication iterated minute rows"),
    )
    with pytest.raises(ValueError, match="source row|budget|ceiling"):
        _publish_aggregate(
            v2_chain,
            minute,
            tmp_path / "aggregate",
            budget=_budget(max_source_rows=1),
        )


def test_unavailable_source_yields_authenticated_unavailable_without_iteration(
    v2_chain, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import market_structure_lab.data.aggregate_publication_v2 as module

    coverage, split, boundary, _ = v2_chain
    empty = tmp_path / "empty"
    empty.mkdir()
    availability = discover_scoped_source_v2(
        boundary=boundary,
        candidate_root=empty,
        audit_ledger_root=tmp_path / "unavailable-discovery-audit",
        output_root=tmp_path / "unavailable-discovery",
    )
    minute = publish_validation_source_v2(
        coverage=coverage,
        split=split,
        boundary=boundary,
        availability=availability,
        source_capability=None,
        publication_root=tmp_path / "minute-unavailable",
        audit_ledger_root=tmp_path / "minute-unavailable-audit",
        max_rows_per_partition=1,
        max_total_rows=1,
        max_total_bytes=1_000,
        max_partitions=1,
    )
    monkeypatch.setattr(
        module,
        "iter_bounded_regular_lines",
        lambda *_args, **_kwargs: pytest.fail("unavailable source iterated minute rows"),
    )
    publication = publish_validation_aggregates_v2(
        minute_publication=minute,
        coverage=coverage,
        split=split,
        boundary=boundary,
        availability=availability,
        output_root=tmp_path / "aggregate-unavailable",
        budget=_budget(),
    )
    assert publication.status is ScopedSourceStatusV2.UNAVAILABLE
    assert publication.failure == "scoped_source_unavailable"
    assert publication.members == ()
    assert publication.row_count == publication.byte_count == 0
    assert (
        verify_validation_aggregate_publication_v2(
            publication,
            minute_publication=minute,
            coverage=coverage,
            split=split,
            boundary=boundary,
            availability=availability,
        )
        == publication
    )


def test_original_parent_output_and_origin_mutations_reject(v2_chain, tmp_path: Path) -> None:
    minute = _publish_minute(v2_chain, tmp_path)
    publication = _publish_aggregate(v2_chain, minute, tmp_path / "aggregate")
    coverage, split, boundary, availability = v2_chain

    parent = minute.publication_root / minute.partitions[0].path
    parent.write_bytes(parent.read_bytes().replace(b'"volume":"1.25"', b'"volume":"2.25"', 1))
    with pytest.raises((RuntimeError, ValueError), match="source|partition|original|bytes"):
        verify_validation_aggregate_publication_v2(
            publication,
            minute_publication=minute,
            coverage=coverage,
            split=split,
            boundary=boundary,
            availability=availability,
        )


def test_output_tamper_coherent_rehash_symlink_and_existing_destination_reject(
    v2_chain, tmp_path: Path
) -> None:
    minute = _publish_minute(v2_chain, tmp_path)
    publication = _publish_aggregate(v2_chain, minute, tmp_path / "aggregate")
    coverage, split, boundary, availability = v2_chain
    part = publication.publication_root / publication.members[0].partitions[0].path
    part.write_bytes(part.read_bytes() + b"{}\n")
    with pytest.raises((RuntimeError, ValueError), match="aggregate|partition|bytes|checksum"):
        verify_validation_aggregate_publication_v2(
            publication,
            minute_publication=minute,
            coverage=coverage,
            split=split,
            boundary=boundary,
            availability=availability,
        )
    with pytest.raises((RuntimeError, ValueError)):
        load_validation_aggregate_publication_v2(
            publication_root=tmp_path / "aggregate",
            expected_aggregate_identity=publication.aggregate_identity,
            minute_publication=minute,
            coverage=coverage,
            split=split,
            boundary=boundary,
            availability=availability,
        )

    destination = tmp_path / "existing"
    destination.mkdir()
    with pytest.raises(FileExistsError):
        _publish_aggregate(v2_chain, minute, destination)
    target = tmp_path / "target"
    target.mkdir()
    symlink = tmp_path / "symlink"
    try:
        symlink.symlink_to(target, target_is_directory=True)
    except OSError:
        pytest.skip("symlinks unavailable")
    with pytest.raises((FileExistsError, RuntimeError)):
        _publish_aggregate(v2_chain, minute, symlink)


def test_caller_constructed_and_reconstructed_verified_objects_reject(
    v2_chain, tmp_path: Path
) -> None:
    minute = _publish_minute(v2_chain, tmp_path)
    publication = _publish_aggregate(v2_chain, minute, tmp_path / "aggregate")
    with pytest.raises((TypeError, ValueError), match="factory|verified|original"):
        replace(publication, byte_count=publication.byte_count)
    copied = copy.copy(publication)
    with pytest.raises((TypeError, ValueError), match="verified|original"):
        tuple(iter_verified_aggregate_rows_v2(copied, budget=_budget()))


def test_registered_capability_rejects_in_memory_origin_and_member_mutation(
    v2_chain, tmp_path: Path
) -> None:
    minute = _publish_minute(v2_chain, tmp_path)
    publication = _publish_aggregate(v2_chain, minute, tmp_path / "aggregate")
    object.__setattr__(publication, "origin_sha256", "0" * 64)
    with pytest.raises(ValueError, match="original|serialization|identity"):
        tuple(iter_verified_aggregate_rows_v2(publication, budget=_budget()))


def test_atomic_commit_rolls_back_on_fsync_failure(
    v2_chain, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import market_structure_lab.data.aggregate_publication_v2 as module

    minute = _publish_minute(v2_chain, tmp_path)
    output = tmp_path / "aggregate"
    monkeypatch.setattr(
        module,
        "_fsync_staged_tree",
        lambda _root: (_ for _ in ()).throw(OSError("interrupted fsync")),
    )
    with pytest.raises(OSError, match="interrupted"):
        _publish_aggregate(v2_chain, minute, output)
    assert not output.exists()


def test_atomic_commit_never_replaces_concurrent_destination(
    v2_chain, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import market_structure_lab.data.aggregate_publication_v2 as module

    minute = _publish_minute(v2_chain, tmp_path)
    output = tmp_path / "aggregate"
    original = module._rename_no_replace  # noqa: SLF001

    def race(stage: Path, destination: Path) -> None:
        destination.mkdir()
        (destination / "winner").write_text("preserve", encoding="ascii")
        original(stage, destination)

    monkeypatch.setattr(module, "_rename_no_replace", race)
    with pytest.raises(FileExistsError):
        _publish_aggregate(v2_chain, minute, output)
    assert (output / "winner").read_text(encoding="ascii") == "preserve"


def test_windows_atomic_commit_uses_write_through_move_without_directory_fsync(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import market_structure_lab.data.aggregate_publication_v2 as module

    stage = tmp_path / "stage"
    stage.mkdir()
    destination = tmp_path / "published"
    moved: list[tuple[Path, Path]] = []

    monkeypatch.setattr(module, "_is_windows_platform", lambda: True)
    monkeypatch.setattr(module, "_fsync_staged_tree", lambda _stage: None)
    monkeypatch.setattr(
        module,
        "durable_move_no_replace",
        lambda source, target: moved.append((source, target)),
    )

    module._publish_stage_no_clobber(stage, destination)  # noqa: SLF001

    assert moved == [(stage, destination)]


def test_chunking_and_fixed_hard_ceilings_are_enforced() -> None:
    import market_structure_lab.data.aggregate_publication_v2 as module

    consumed = 0

    def counting():
        nonlocal consumed
        for value in range(10):
            consumed += 1
            yield value

    chunks = module._iter_chunks(counting(), maximum=3)  # noqa: SLF001
    assert next(chunks) == (0, 1, 2)
    assert consumed == 3
    with pytest.raises(ValueError, match="hard ceiling"):
        _budget(max_source_rows=50_000_001)


def test_reader_budget_rejects_before_parent_rederivation_or_output_iteration(
    v2_chain, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import market_structure_lab.data.aggregate_publication_v2 as module

    minute = _publish_minute(v2_chain, tmp_path)
    publication = _publish_aggregate(v2_chain, minute, tmp_path / "aggregate")
    monkeypatch.setattr(
        module,
        "_verify_rederived_members",
        lambda *_args, **_kwargs: pytest.fail("reader rederived minute parents"),
    )
    monkeypatch.setattr(
        module,
        "iter_bounded_regular_lines",
        lambda *_args, **_kwargs: pytest.fail("reader traversed output rows"),
    )

    with pytest.raises(ValueError, match="row budget"):
        tuple(
            iter_verified_aggregate_rows_v2(
                publication,
                budget=_budget(max_aggregate_rows=1),
            )
        )


def test_sealed_series_reader_returns_one_contiguous_original_segment(
    v2_chain, tmp_path: Path
) -> None:
    minute = _publish_minute(v2_chain, tmp_path)
    publication = _publish_aggregate(v2_chain, minute, tmp_path / "aggregate")
    member = publication.members[0]
    key = issue_aggregate_series_key_v2(
        publication,
        symbol=member.symbol,
        interval_index=member.interval_index,
        target_timeframe=member.target_timeframe,
        segment_id=0,
    )

    series = open_verified_aggregate_series_v2(
        publication,
        key,
        _budget(),
    )

    assert isinstance(key, AggregateSeriesKeyV2)
    assert isinstance(series, VerifiedAggregateSeriesV2)
    assert series.key is key
    assert series.rows
    assert all(row.segment_id == 0 for row in series.rows)
    assert all(
        following.timestamp - prior.timestamp
        == timedelta(minutes={"1h": 60, "4h": 240}[key.target_timeframe])
        for prior, following in zip(series.rows, series.rows[1:], strict=False)
    )
    assert verify_verified_aggregate_series_v2(series, budget=_budget()) is series


def test_sealed_series_exposes_exact_original_publication_provenance(
    v2_chain, tmp_path: Path
) -> None:
    minute = _publish_minute(v2_chain, tmp_path)
    publication = _publish_aggregate(v2_chain, minute, tmp_path / "aggregate")
    member = publication.members[0]
    key = issue_aggregate_series_key_v2(
        publication,
        symbol=member.symbol,
        interval_index=member.interval_index,
        target_timeframe=member.target_timeframe,
        segment_id=0,
    )
    series = open_verified_aggregate_series_v2(publication, key, _budget())

    assert (
        series.aggregate_publication_sha256
        == hashlib.sha256(publication.canonical_bytes).hexdigest()
    )
    assert series.aggregate_publication_sha256 != series.series_identity
    assert series.aggregate_identity == publication.aggregate_identity
    assert series.aggregate_identity.value == publication.aggregate_identity.value
    assert series.budget_sha256 == publication.budget_sha256

    with pytest.raises((TypeError, ValueError), match="registered|original|factory"):
        verify_verified_aggregate_series_v2(copy.copy(series), budget=_budget())
    object.__setattr__(series, "aggregate_publication_sha256", "0" * 64)
    with pytest.raises(ValueError, match="identity|original|serialization"):
        verify_verified_aggregate_series_v2(series, budget=_budget())


@pytest.mark.parametrize("reader_kind", ("public_iterator", "sealed_series"))
def test_aggregate_readers_never_yield_replaced_partition_bytes(
    v2_chain,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    reader_kind: str,
) -> None:
    import market_structure_lab.data.aggregate_publication_v2 as module
    from market_structure_lab.core import artifact_io

    minute = _publish_minute(v2_chain, tmp_path)
    publication = _publish_aggregate(v2_chain, minute, tmp_path / "aggregate")
    member = publication.members[0]
    partition = member.partitions[0]
    partition_path = publication.publication_root / partition.path
    original_bytes = partition_path.read_bytes()
    replacement_path = tmp_path / f"{reader_kind}-replacement.jsonl"
    replacement_path.write_bytes(_coherent_alternative_partition(original_bytes))
    original_rows = tuple(
        module.AggregateRowV2.from_dict(json.loads(line)) for line in original_bytes.splitlines()
    )

    original_verify = module.verify_validation_aggregate_publication_v2
    original_open = artifact_io._open_regular  # noqa: SLF001
    armed = False
    replaced = False

    def verify_then_arm(*args: object, **kwargs: object):
        nonlocal armed
        result = original_verify(*args, **kwargs)  # type: ignore[arg-type]
        armed = True
        return result

    def replace_entry() -> None:
        nonlocal replaced
        os.replace(replacement_path, partition_path)
        replaced = True

    def racing_open(path: Path):
        handle = original_open(path)
        if path == partition_path and armed and not replaced:
            return _ReplaceOnEof(handle, replace_entry)
        return handle

    monkeypatch.setattr(module, "verify_validation_aggregate_publication_v2", verify_then_arm)
    monkeypatch.setattr(artifact_io, "_open_regular", racing_open)

    if reader_kind == "public_iterator":
        observed = tuple(
            row
            for row in iter_verified_aggregate_rows_v2(publication, budget=_budget())
            if (
                row.symbol,
                row.interval_index,
                row.target_timeframe,
                row.segment_id,
            )
            == (
                member.symbol,
                member.interval_index,
                member.target_timeframe,
                partition.segment_id,
            )
        )
    else:
        key = issue_aggregate_series_key_v2(
            publication,
            symbol=member.symbol,
            interval_index=member.interval_index,
            target_timeframe=member.target_timeframe,
            segment_id=partition.segment_id,
        )
        observed = open_verified_aggregate_series_v2(publication, key, _budget()).rows

    assert replaced
    assert observed == original_rows


def test_sealed_series_retains_registered_parents_until_series_collection(
    v2_chain,
    tmp_path: Path,
) -> None:
    import market_structure_lab.data.aggregate_publication_v2 as module

    def issue_only_series() -> VerifiedAggregateSeriesV2:
        minute = _publish_minute(v2_chain, tmp_path)
        publication = _publish_aggregate(v2_chain, minute, tmp_path / "aggregate")
        member = publication.members[0]
        key = issue_aggregate_series_key_v2(
            publication,
            symbol=member.symbol,
            interval_index=member.interval_index,
            target_timeframe=member.target_timeframe,
            segment_id=0,
        )
        return open_verified_aggregate_series_v2(publication, key, _budget())

    series = issue_only_series()
    identifier = id(series)
    reference = weakref.ref(series)
    gc.collect()

    assert verify_verified_aggregate_series_v2(series, budget=_budget()) is series
    assert identifier in module._VERIFIED_SERIES  # noqa: SLF001

    del series
    gc.collect()

    assert reference() is None
    assert identifier not in module._VERIFIED_SERIES  # noqa: SLF001


def test_sealed_series_reuses_its_exact_registered_open_budget(
    v2_chain,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import market_structure_lab.data.aggregate_publication_v2 as module

    minute = _publish_minute(v2_chain, tmp_path)
    open_budget = _budget(max_rows_per_partition=2)
    publication = _publish_aggregate(
        v2_chain,
        minute,
        tmp_path / "aggregate",
        budget=open_budget,
    )
    member = next(item for item in publication.members if len(item.partitions) > 1)
    key = issue_aggregate_series_key_v2(
        publication,
        symbol=member.symbol,
        interval_index=member.interval_index,
        target_timeframe=member.target_timeframe,
        segment_id=member.partitions[0].segment_id,
    )
    series = open_verified_aggregate_series_v2(publication, key, open_budget)
    maximum_partition_rows = max(item.row_count for item in member.partitions)
    assert series.row_count > maximum_partition_rows
    monkeypatch.setitem(
        module._HARD_BUDGET_CEILINGS,  # noqa: SLF001
        "max_rows_per_partition",
        maximum_partition_rows,
    )

    verifier = getattr(module, "verify_original_aggregate_series_v2", None)
    assert verifier is not None, "missing no-argument original-series verifier"
    assert series.verify_original() is series
    assert verifier(series) is series

    with pytest.raises((TypeError, ValueError), match="registered|original|factory"):
        copy.copy(series).verify_original()

    class Lookalike:
        def __getattr__(self, name: str):
            return getattr(series, name)

    with pytest.raises(TypeError, match="VerifiedAggregateSeriesV2|verified.*series"):
        verifier(Lookalike())


def test_series_key_and_series_are_nominal_registered_originals(v2_chain, tmp_path: Path) -> None:
    minute = _publish_minute(v2_chain, tmp_path)
    publication = _publish_aggregate(v2_chain, minute, tmp_path / "aggregate")
    member = publication.members[0]
    key = issue_aggregate_series_key_v2(
        publication,
        symbol=member.symbol,
        interval_index=member.interval_index,
        target_timeframe=member.target_timeframe,
        segment_id=0,
    )
    series = open_verified_aggregate_series_v2(publication, key, _budget())

    with pytest.raises(TypeError, match="factory"):
        AggregateSeriesKeyV2(
            symbol=key.symbol,
            interval_index=key.interval_index,
            target_timeframe=key.target_timeframe,
            segment_id=key.segment_id,
        )
    with pytest.raises((TypeError, ValueError), match="registered|original|factory"):
        open_verified_aggregate_series_v2(publication, copy.copy(key), _budget())
    with pytest.raises((TypeError, ValueError), match="registered|original|factory"):
        verify_verified_aggregate_series_v2(copy.copy(series), budget=_budget())
    object.__setattr__(series.rows[0], "close", Decimal("999"))
    with pytest.raises(ValueError, match="identity|original|serialization"):
        verify_verified_aggregate_series_v2(series, budget=_budget())


def test_series_budget_and_missing_or_mixed_segment_reject_before_open(
    v2_chain, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    minute = _publish_minute(v2_chain, tmp_path)
    publication = _publish_aggregate(v2_chain, minute, tmp_path / "aggregate")
    member = publication.members[0]
    key = issue_aggregate_series_key_v2(
        publication,
        symbol=member.symbol,
        interval_index=member.interval_index,
        target_timeframe=member.target_timeframe,
        segment_id=0,
    )
    monkeypatch.setattr(
        "market_structure_lab.data.aggregate_publication_v2."
        "verify_validation_aggregate_publication_v2",
        lambda *_args, **_kwargs: pytest.fail("budget failure opened publication"),
    )
    with pytest.raises(ValueError, match="row budget"):
        open_verified_aggregate_series_v2(
            publication,
            key,
            _budget(max_aggregate_rows=1),
        )

    missing = issue_aggregate_series_key_v2(
        publication,
        symbol=member.symbol,
        interval_index=member.interval_index,
        target_timeframe=member.target_timeframe,
        segment_id=member.segment_count,
    )
    with pytest.raises(ValueError, match="exactly one|segment"):
        open_verified_aggregate_series_v2(publication, missing, _budget())
