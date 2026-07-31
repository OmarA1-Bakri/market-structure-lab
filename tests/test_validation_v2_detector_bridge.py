from __future__ import annotations

from collections.abc import Iterator
import copy
from dataclasses import fields, replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
import hashlib
from pathlib import Path

import pytest

from market_structure_lab.core.identity import hash_json
from market_structure_lab.data import aggregate_publication_v2 as aggregate_v2
from market_structure_lab.data.aggregate_publication_v2 import (
    AggregatePublicationBudgetV2,
    AggregatePublicationV2,
    iter_verified_aggregate_rows_v2,
    publish_validation_aggregates_v2,
)
from market_structure_lab.data.validation_source_v2 import (
    CanonicalMinuteRowV2,
    discover_scoped_source_v2,
    publish_validation_source_v2,
)
from market_structure_lab.research import candidates as candidate_module
from market_structure_lab.research import models as model_module
from market_structure_lab.research.candidates import detect_candidate_signals
from market_structure_lab.research.models import VALIDATION_SLOT_ROSTER
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


def _aggregate_budget() -> AggregatePublicationBudgetV2:
    return AggregatePublicationBudgetV2(
        max_source_rows=100_000,
        max_source_bytes=100_000_000,
        max_parent_partitions=10_000,
        max_source_rows_per_chunk=480,
        max_members=1_000,
        max_aggregate_rows=20_000,
        max_rows_per_partition=31,
        max_output_bytes=100_000_000,
        max_output_files=10_000,
    )


def _trusted_source_availability(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, boundary):
    from market_structure_lab.data import validation_source_v2 as source_module

    candidates = tmp_path / "source-candidates"
    candidates.mkdir()
    source_identity = "content-addressed-detector-bridge-source"
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
    (candidates / "candidate.json").write_bytes(publication_json_bytes(descriptor))
    monkeypatch.setattr(
        source_module,
        "_TRUSTED_VERIFIER_EVIDENCE_SHA256",
        frozenset({hashlib.sha256(verifier_bytes).hexdigest()}),
    )
    return discover_scoped_source_v2(
        boundary=boundary,
        candidate_root=candidates,
        audit_ledger_root=tmp_path / "source-discovery-audit",
        output_root=tmp_path / "source-discovery",
    )


def _minute_requests(boundary) -> tuple[tuple[CanonicalMinuteRowV2, ...], ...]:
    requests = []
    for symbol in boundary.allowed_symbols:
        for interval_index, interval in enumerate(boundary.allowed_intervals):
            minute_count = 76 * 60 if interval_index == 0 else 4 * 60
            rows = []
            for offset in range(minute_count):
                hour = offset // 60
                close = Decimal("100") if hour < 72 else Decimal("200")
                rows.append(
                    CanonicalMinuteRowV2(
                        timestamp=interval.start + timedelta(minutes=offset),
                        symbol=symbol,
                        timeframe="1m",
                        open=close,
                        high=close + Decimal("1"),
                        low=close - Decimal("1"),
                        close=close,
                        volume=Decimal("1"),
                    )
                )
            requests.append(tuple(rows))
    return tuple(requests)


@pytest.fixture(scope="module")
def aggregate_publication(
    tmp_path_factory: pytest.TempPathFactory,
) -> Iterator[AggregatePublicationV2]:
    tmp_path = tmp_path_factory.mktemp("v2-detector-bridge")
    monkeypatch = pytest.MonkeyPatch()
    from market_structure_lab.data import validation_source_v2 as source_module

    coverage = SourceCoveragePublicationV2.freeze(
        raw_dump=RawDumpIdentityV2(
            "a" * 64, 1, "b" * 64, "public.candles:detector-bridge", "mapping-v2"
        ),
        reconciliation=ReconciliationAuthorityV2(
            "c" * 64, ("d" * 64,), ("e" * 64,), "rr-000008-promoted-only"
        ),
        compatibility_metadata_sha256="f" * 64,
        entries=tuple(
            SourceCoverageEntryV2(
                symbol,
                datetime(2025, 1, 1, tzinfo=UTC),
                datetime(2025, 6, 30, tzinfo=UTC),
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
        policy=SplitPolicyV2(6, 180, 1, 2, "detector-bridge-public-salt", 1, 1, ("1m", "1h", "4h")),
    )
    boundary = issue_development_read_boundary_v2(coverage, split)
    availability = _trusted_source_availability(tmp_path, monkeypatch, boundary)
    capability = source_module._issue_test_minute_source_capability_v2(  # noqa: SLF001
        boundary=boundary,
        availability=availability,
        request_rows=_minute_requests(boundary),
        fail_request_index=None,
    )
    minute = publish_validation_source_v2(
        coverage=coverage,
        split=split,
        boundary=boundary,
        availability=availability,
        source_capability=capability,
        publication_root=tmp_path / "minute",
        audit_ledger_root=tmp_path / "minute-audit",
        max_rows_per_partition=240,
        max_total_rows=100_000,
        max_total_bytes=100_000_000,
        max_partitions=10_000,
    )
    publication = publish_validation_aggregates_v2(
        minute_publication=minute,
        coverage=coverage,
        split=split,
        boundary=boundary,
        availability=availability,
        output_root=tmp_path / "aggregate",
        budget=_aggregate_budget(),
    )
    assert publication.final_scope_attempts == publication.final_rows == 0
    assert publication.final_access_records == 0
    try:
        yield publication
    finally:
        monkeypatch.undo()


def _bridge_api():
    required = {
        "VerifiedCandidateSeriesV2": getattr(candidate_module, "VerifiedCandidateSeriesV2", None),
        "bridge_verified_aggregate_series_v2": getattr(
            candidate_module, "bridge_verified_aggregate_series_v2", None
        ),
        "candidate_definition_for_verified_series": getattr(
            model_module, "candidate_definition_for_verified_series", None
        ),
    }
    missing = tuple(name for name, value in required.items() if value is None)
    assert not missing, f"missing sealed V2 detector bridge API: {missing}"
    return required


def _open_series(
    publication: AggregatePublicationV2,
    *,
    interval_index: int = 0,
    target_timeframe: str = "1h",
):
    key_issuer = getattr(aggregate_v2, "issue_aggregate_series_key_v2", None)
    opener = getattr(aggregate_v2, "open_verified_aggregate_series_v2", None)
    assert key_issuer is not None, "missing verifier-issued AggregateSeriesKeyV2 API"
    assert opener is not None, "missing verifier-issued V2 aggregate series opener"
    key = key_issuer(
        publication,
        symbol=publication.allowed_symbols[0],
        interval_index=interval_index,
        target_timeframe=target_timeframe,
        segment_id=0,
    )
    return opener(publication, key, _aggregate_budget())


def _vs_0001():
    return next(slot for slot in VALIDATION_SLOT_ROSTER if slot.slot_id == "VS-0001")


def _family_b_slot():
    return next(slot for slot in VALIDATION_SLOT_ROSTER if slot.slot_id == "VS-0025")


def _profile_stream(bridged):
    stream_type = candidate_module.VerifiedProfileStream
    source_row_count = len(bridged.bars) * 60
    payload = {
        "aggregate_series_sha256": bridged.series_sha256,
        "validation_programme_id": "VP-" + "1" * 64,
        "work_budget_sha256": "2" * 64,
        "source_minute_publication_sha256": "3" * 64,
        "profile_config_sha256": "4" * 64,
        "bin_metadata_sha256": "5" * 64,
        "bin_step": 1.0,
        "bin_origin": 0.0,
        "bin_definition_id": "fixed-step:1.0:origin=0.0:source=test",
        "source_price_precision_manifest_sha256": "6" * 64,
        "window_hours": 24,
        "source_row_count": source_row_count,
        "source_sha256": "7" * 64,
        "profile_active_bin_cells": 0,
        "profile_source_id_bytes": source_row_count * 64,
        "profile_config_bytes": 1,
        "profile_serialized_bytes": 0,
        "ordered_profile_ids": (),
    }
    stream_sha256 = hash_json(
        "verified-candidate-profile-stream-v1",
        payload,
    )
    return stream_type(
        **payload,
        profiles=(),
        stream_sha256=stream_sha256,
        seal=candidate_module._VERIFIED_PROFILE_STREAM_SEAL,  # noqa: SLF001
    )


def _unregistered_profile_clone(profile, clone_type=None):
    target_type = clone_type or type(profile)
    clone = object.__new__(target_type)
    for item in fields(profile):
        object.__setattr__(clone, item.name, getattr(profile, item.name))
    return clone


def _family_b_definition(bridged, profile):
    parent = model_module.candidate_definition_for_verified_series(_vs_0001(), bridged)
    return model_module.candidate_definition_for_verified_series(
        _family_b_slot(),
        bridged,
        parent_a_candidate=parent,
        profile_stream=profile,
    )


def test_public_detector_bridge_api_is_exposed() -> None:
    _bridge_api()


def test_verifier_issued_v2_series_uses_the_existing_vs_0001_detector_formula(
    aggregate_publication: AggregatePublicationV2,
) -> None:
    api = _bridge_api()
    bridged = api["bridge_verified_aggregate_series_v2"](_open_series(aggregate_publication))
    definition = api["candidate_definition_for_verified_series"](_vs_0001(), bridged)

    [signal] = detect_candidate_signals(definition, bridged)

    assert isinstance(bridged, api["VerifiedCandidateSeriesV2"])
    assert not hasattr(bridged.bars[0], "source_row_ids")
    assert not hasattr(bridged.bars[0], "source_sha256")
    assert not hasattr(bridged.bars[0], "parent_snapshot_sha256")
    assert not hasattr(bridged.bars[0], "to_dict")
    assert signal.candidate_slot_id == "VS-0001"
    assert signal.information_cutoff == bridged.bars[72].bar_close
    assert signal.feature_start == bridged.bars[0].timestamp


def test_bridge_capability_rejects_direct_replace_and_lookalike_objects(
    aggregate_publication: AggregatePublicationV2,
) -> None:
    api = _bridge_api()
    verified_v2 = _open_series(aggregate_publication)

    class AggregateLookalike:
        def __getattr__(self, name: str):
            return getattr(verified_v2, name)

    with pytest.raises(TypeError, match="VerifiedAggregateSeriesV2|verified.*series|capability"):
        api["bridge_verified_aggregate_series_v2"](AggregateLookalike())

    bridged = api["bridge_verified_aggregate_series_v2"](verified_v2)
    constructor = {item.name: getattr(bridged, item.name) for item in fields(bridged) if item.init}

    with pytest.raises((TypeError, ValueError), match="factory|seal|verifier|capability"):
        type(bridged)(**constructor)
    with pytest.raises((TypeError, ValueError), match="factory|seal|verifier|capability"):
        replace(bridged)

    class Lookalike:
        def __getattr__(self, name: str):
            return getattr(bridged, name)

    with pytest.raises(TypeError, match="VerifiedCandidateSeriesV2|verified.*series|capability"):
        api["candidate_definition_for_verified_series"](_vs_0001(), Lookalike())
    definition = api["candidate_definition_for_verified_series"](_vs_0001(), bridged)
    with pytest.raises(TypeError, match="VerifiedCandidateSeriesV2|verified.*series|capability"):
        detect_candidate_signals(definition, Lookalike())


def test_family_b_definition_rejects_subclass_copy_and_mutated_profile_streams(
    aggregate_publication: AggregatePublicationV2,
) -> None:
    bridged = candidate_module.bridge_verified_aggregate_series_v2(
        _open_series(aggregate_publication)
    )
    original = _profile_stream(bridged)

    class ProfileSubclass(type(original)):
        pass

    subclass = _unregistered_profile_clone(original, ProfileSubclass)
    copied = copy.copy(original)
    mutated = copy.copy(original)
    object.__setattr__(mutated, "bin_step", 2.0)

    for forged in (subclass, copied, mutated):
        with pytest.raises((TypeError, ValueError), match="profile.*factory|registered|original"):
            _family_b_definition(bridged, forged)


def test_family_b_detection_revalidates_exact_registered_profile_stream(
    aggregate_publication: AggregatePublicationV2,
) -> None:
    bridged = candidate_module.bridge_verified_aggregate_series_v2(
        _open_series(aggregate_publication)
    )
    original = _profile_stream(bridged)
    definition = _family_b_definition(bridged, original)

    class ProfileSubclass(type(original)):
        pass

    subclass = _unregistered_profile_clone(original, ProfileSubclass)
    copied = copy.copy(original)
    mutated = copy.copy(original)
    object.__setattr__(mutated, "bin_step", 2.0)

    class Lookalike:
        def __getattr__(self, name: str):
            return getattr(original, name)

    for forged in (subclass, copied, mutated, Lookalike()):
        with pytest.raises((TypeError, ValueError), match="profile.*factory|registered|original"):
            detect_candidate_signals(definition, bridged, profile_stream=forged)


def test_original_aggregate_byte_mutation_rejects_before_detection(
    aggregate_publication: AggregatePublicationV2,
) -> None:
    api = _bridge_api()
    verified_v2 = _open_series(aggregate_publication)
    bridged = api["bridge_verified_aggregate_series_v2"](verified_v2)
    definition = api["candidate_definition_for_verified_series"](_vs_0001(), bridged)
    member = next(
        item
        for item in aggregate_publication.members
        if item.symbol == aggregate_publication.allowed_symbols[0]
        and item.interval_index == 0
        and item.target_timeframe == "1h"
    )
    partition_path = aggregate_publication.publication_root / member.partitions[0].path
    original = partition_path.read_bytes()
    partition_path.write_bytes(original + b" ")
    try:
        with pytest.raises(ValueError, match="original|byte|checksum|changed|verified"):
            api["bridge_verified_aggregate_series_v2"](verified_v2)
        with pytest.raises(ValueError, match="original|byte|checksum|changed|verified"):
            detect_candidate_signals(definition, bridged)
    finally:
        partition_path.write_bytes(original)


def test_definition_and_signal_bind_exact_v2_publication_series_segment_and_timeframe(
    aggregate_publication: AggregatePublicationV2,
) -> None:
    api = _bridge_api()
    bridged = api["bridge_verified_aggregate_series_v2"](_open_series(aggregate_publication))
    definition = api["candidate_definition_for_verified_series"](_vs_0001(), bridged)
    [signal] = detect_candidate_signals(definition, bridged)

    assert bridged.publication_sha256 == hashlib.sha256(
        aggregate_publication.canonical_bytes
    ).hexdigest()
    assert bridged.aggregate_identity == aggregate_publication.aggregate_identity.value
    assert bridged.aggregate_budget_sha256 == aggregate_publication.budget_sha256
    assert bridged.interval_index == 0
    assert definition.source_publication_sha256 == bridged.publication_sha256
    assert definition.source_series_sha256 == bridged.series_sha256
    assert definition.source_segment_id == bridged.segment_id
    assert definition.timeframe == bridged.target_timeframe == "1h"
    assert signal.source_publication_sha256 == bridged.publication_sha256
    assert signal.source_series_sha256 == bridged.series_sha256
    assert signal.segment_id == bridged.segment_id
    assert signal.timeframe == bridged.target_timeframe
    different_interval = api["bridge_verified_aggregate_series_v2"](
        _open_series(aggregate_publication, interval_index=1)
    )
    with pytest.raises(ValueError, match="does not own|publication|series|segment|timeframe"):
        detect_candidate_signals(definition, different_interval)


def test_v1_and_v2_identical_bar_clocks_emit_identical_vs_0001_signal_clocks(
    aggregate_publication: AggregatePublicationV2,
) -> None:
    import test_research_candidates as v1_candidate_tests

    api = _bridge_api()
    bridged = api["bridge_verified_aggregate_series_v2"](_open_series(aggregate_publication))
    v2_definition = api["candidate_definition_for_verified_series"](_vs_0001(), bridged)
    v1_series = v1_candidate_tests._series(bridged.bars)
    v1_definition = model_module.candidate_definition_for_slot(_vs_0001(), v1_series)

    v2_signals = detect_candidate_signals(v2_definition, bridged)
    v1_signals = detect_candidate_signals(v1_definition, v1_series)

    assert [
        (item.direction, item.feature_start, item.information_cutoff, item.legal_entry)
        for item in v2_signals
    ] == [
        (item.direction, item.feature_start, item.information_cutoff, item.legal_entry)
        for item in v1_signals
    ]


def test_long_v2_fixture_is_a_valid_original_byte_publication(
    aggregate_publication: AggregatePublicationV2,
) -> None:
    rows = tuple(
        row
        for row in iter_verified_aggregate_rows_v2(
            aggregate_publication, budget=_aggregate_budget()
        )
        if row.symbol == aggregate_publication.allowed_symbols[0]
        and row.interval_index == 0
        and row.target_timeframe == "1h"
        and row.segment_id == 0
    )

    assert len(rows) == 76
    assert tuple(row.close for row in rows[:72]) == (Decimal("100"),) * 72
    assert tuple(row.close for row in rows[72:]) == (Decimal("200"),) * 4
