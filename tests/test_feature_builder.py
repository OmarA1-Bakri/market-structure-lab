from __future__ import annotations

import copy
import gc
import json
import weakref
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from math import log, sqrt
from pathlib import Path
from threading import Event, Thread

import pytest
import market_structure_lab.features as features_package

from market_structure_lab.auction.engine import (
    AuctionLocation,
    AuctionSnapshot,
    StructuralEvent,
    StructuralEventKind,
)
from market_structure_lab.auction.replay import snapshot_stream_sha256
from market_structure_lab.auction.models import AuctionCandle
import market_structure_lab.features.builder as feature_builder_module

from market_structure_lab.features.builder import (
    FeatureBuildBatch,
    FeatureBuildBatchLease,
    FeatureBuildBatchMetadata,
    FeatureBuilder,
)
from market_structure_lab.features.builtin import (
    BUILTIN_DEFINITIONS,
    builtin_feature_registry,
)
from market_structure_lab.features.models import FeatureRow
from market_structure_lab.features.registry import (
    BUILTIN_FEATURE_BUILDER_ID,
    BUILTIN_FEATURE_BUILDER_VERSION,
    FeatureRegistry,
)
from market_structure_lab.profiles.binning import FixedStepBins
from market_structure_lab.profiles.models import ProfileSnapshot
from market_structure_lab.structure.nodes import NodeKind, ProfileNode

BASE = datetime(2025, 1, 1, tzinfo=UTC)
BINS = FixedStepBins(step=1.0)
GOLDEN = Path(__file__).parent / "fixtures" / "phase3" / "feature_golden_v1.json"


def test_builder_identity_and_output_match_every_registered_field_contract() -> None:
    builder = FeatureBuilder()
    row = builder.update(snapshot(0))

    assert builder.builder_id == BUILTIN_FEATURE_BUILDER_ID
    assert builder.builder_version == BUILTIN_FEATURE_BUILDER_VERSION
    assert tuple(row.values) == builder.registry.names
    assert all(
        definition.builder_id == builder.builder_id
        and definition.builder_version == builder.builder_version
        and definition.future_outcome_prohibited is True
        and definition.source_fields
        and len(definition.dependency_contract_sha256) == 64
        for definition in builder.registry.definitions
    )


def test_builder_emits_immutable_replayable_producer_bound_batch(tmp_path: Path) -> None:
    builder = FeatureBuilder()
    batch = builder.build_batch(
        (snapshot(index, close=100.0 + index) for index in range(3)),
        artifact_path=tmp_path / "feature-output.jsonl",
        maximum_rows=3,
    )

    first = tuple(batch.iter_rows())
    second = tuple(batch.iter_rows())

    assert first == second
    assert batch.row_count == 3
    assert batch.builder_id == builder.builder_id
    assert batch.builder_version == builder.builder_version
    assert batch.feature_registry_sha256 == builder.registry.sha256
    assert batch.dependency_contract_sha256 == builder.registry.dependency_contract_sha256
    assert batch.artifact_sha256 == batch.content_sha256


def _builder_state(builder: FeatureBuilder) -> tuple[object, ...]:
    return (
        tuple(builder._history),
        builder._latest,
        builder._last_location,
        builder._location_dwell,
    )


def test_batch_rejects_undeclared_preseeded_builder_context_without_mutation(
    tmp_path: Path,
) -> None:
    builder = FeatureBuilder()
    builder.update(snapshot(0))
    before = _builder_state(builder)
    destination = tmp_path / "preseeded.jsonl"

    with pytest.raises(ValueError, match="empty continuation context"):
        builder.build_batch(
            [snapshot(1)],
            artifact_path=destination,
            maximum_rows=1,
        )

    assert _builder_state(builder) == before
    assert not destination.exists()
    assert not destination.with_name(f".{destination.name}.tmp").exists()


def test_batch_binds_exact_ordered_snapshot_stream_and_empty_starting_context(
    tmp_path: Path,
) -> None:
    snapshots = (snapshot(0), snapshot(1))
    first = FeatureBuilder().build_batch(
        snapshots,
        artifact_path=tmp_path / "first.jsonl",
        maximum_rows=2,
    )
    same_output_different_input = FeatureBuilder().build_batch(
        (replace(snapshots[0], window_id="different-window-instance"), snapshots[1]),
        artifact_path=tmp_path / "different-input.jsonl",
        maximum_rows=2,
    )
    second = FeatureBuilder().build_batch(
        snapshots,
        artifact_path=tmp_path / "second.jsonl",
        maximum_rows=2,
    )

    assert first.input_snapshot_stream_sha256 == snapshot_stream_sha256(snapshots)
    assert first.input_snapshot_stream_sha256 == second.input_snapshot_stream_sha256
    assert first.starting_context_sha256 == second.starting_context_sha256
    assert first.content_sha256 == same_output_different_input.content_sha256
    assert (
        first.input_snapshot_stream_sha256
        != same_output_different_input.input_snapshot_stream_sha256
    )
    lease = FeatureBuildBatch.verify_issued(first)
    metadata = FeatureBuildBatch.resolve_lease(first, lease)
    assert metadata.input_snapshot_stream_sha256 == first.input_snapshot_stream_sha256
    assert metadata.starting_context_sha256 == first.starting_context_sha256


def test_failed_batch_restores_exact_state_removes_output_and_retries_cleanly(
    tmp_path: Path,
) -> None:
    builder = FeatureBuilder()
    before = _builder_state(builder)
    failed_path = tmp_path / "failed.jsonl"

    def failing_snapshots():
        yield snapshot(0)
        yield object()

    with pytest.raises(TypeError, match="AuctionSnapshot"):
        builder.build_batch(
            failing_snapshots(),
            artifact_path=failed_path,
            maximum_rows=2,
        )

    assert _builder_state(builder) == before
    assert not failed_path.exists()
    assert not failed_path.with_name(f".{failed_path.name}.tmp").exists()

    inputs = (snapshot(0), snapshot(1))
    retry = builder.build_batch(
        inputs,
        artifact_path=tmp_path / "retry.jsonl",
        maximum_rows=2,
    )
    clean = FeatureBuilder().build_batch(
        inputs,
        artifact_path=tmp_path / "clean.jsonl",
        maximum_rows=2,
    )
    assert tuple(retry.iter_rows()) == tuple(clean.iter_rows())
    assert retry.content_sha256 == clean.content_sha256
    assert retry.input_snapshot_stream_sha256 == clean.input_snapshot_stream_sha256
    assert retry.starting_context_sha256 == clean.starting_context_sha256


def test_failed_final_issuance_revokes_batch_and_restores_output_and_builder(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    builder = FeatureBuilder()
    before = _builder_state(builder)
    destination = tmp_path / "issuance-failure.jsonl"
    original_issue = feature_builder_module._issue_feature_build_batch
    captured: list[FeatureBuildBatch] = []

    def fail_after_issue(batch: FeatureBuildBatch) -> FeatureBuildBatch:
        captured.append(original_issue(batch))
        raise RuntimeError("issuance fault")

    monkeypatch.setattr(feature_builder_module, "_issue_feature_build_batch", fail_after_issue)
    with pytest.raises(RuntimeError, match="issuance fault"):
        builder.build_batch(
            [snapshot(0)],
            artifact_path=destination,
            maximum_rows=1,
        )

    assert _builder_state(builder) == before
    assert not destination.exists()
    assert not destination.with_name(f".{destination.name}.tmp").exists()
    assert len(captured) == 1
    with pytest.raises(ValueError, match="exact issued FeatureBuildBatch"):
        tuple(captured[0].iter_rows())


def test_batch_replay_uses_authoritative_absolute_path_after_working_directory_change(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    build_root = tmp_path / "build-root"
    (build_root / "batches").mkdir(parents=True)
    other_root = tmp_path / "other-root"
    other_root.mkdir()
    monkeypatch.chdir(build_root)
    batch = FeatureBuilder().build_batch(
        [snapshot(0)],
        artifact_path=Path("batches") / "rows.jsonl",
        maximum_rows=1,
    )

    assert batch.artifact_path.is_absolute()
    assert batch.artifact_path == build_root / "batches" / "rows.jsonl"
    monkeypatch.chdir(other_root)

    assert tuple(batch.iter_rows()) == (FeatureBuilder().update(snapshot(0)),)


def test_feature_batch_rejects_aggregate_bytes_before_record_write(tmp_path: Path) -> None:
    builder = FeatureBuilder()
    before = _builder_state(builder)
    destination = tmp_path / "oversized.jsonl"

    with pytest.raises(ValueError, match="aggregate byte budget"):
        builder.build_batch(
            [snapshot(0)],
            artifact_path=destination,
            maximum_rows=1,
            maximum_total_bytes=1,
        )

    assert _builder_state(builder) == before
    assert not destination.exists()
    assert not destination.with_name(f".{destination.name}.tmp").exists()


def _batch_constructor_fields(batch: FeatureBuildBatch) -> dict[str, object]:
    return {
        "artifact_path": batch.artifact_path,
        "artifact_sha256": batch.artifact_sha256,
        "content_sha256": batch.content_sha256,
        "input_snapshot_stream_sha256": batch.input_snapshot_stream_sha256,
        "starting_context_sha256": batch.starting_context_sha256,
        "row_count": batch.row_count,
        "builder_id": batch.builder_id,
        "builder_version": batch.builder_version,
        "feature_registry_sha256": batch.feature_registry_sha256,
        "dependency_contract_sha256": batch.dependency_contract_sha256,
        "registry_id": batch.registry_id,
        "maximum_row_bytes": batch.maximum_row_bytes,
        "maximum_total_bytes": batch.maximum_total_bytes,
    }


@pytest.mark.parametrize("forgery_kind", ("replace", "copy", "deepcopy", "manual", "subclass"))
def test_only_exact_issued_batch_instance_can_replay(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    forgery_kind: str,
) -> None:
    issued = FeatureBuilder().build_batch(
        [snapshot(0)],
        artifact_path=tmp_path / "issued.jsonl",
        maximum_rows=1,
    )
    fields = _batch_constructor_fields(issued)
    if forgery_kind == "replace":
        forged = replace(issued)
    elif forgery_kind == "copy":
        forged = copy.copy(issued)
    elif forgery_kind == "deepcopy":
        forged = copy.deepcopy(issued)
    elif forgery_kind == "manual":
        forged = FeatureBuildBatch(**fields)
    else:

        class ForgedFeatureBuildBatch(FeatureBuildBatch):
            pass

        forged = ForgedFeatureBuildBatch(**fields)

    def artifact_was_read(*args: object, **kwargs: object) -> str:
        raise AssertionError("unissued feature batch reached artifact replay")

    monkeypatch.setattr(feature_builder_module, "iter_bounded_regular_lines", artifact_was_read)
    with pytest.raises((TypeError, ValueError), match="exact issued FeatureBuildBatch"):
        tuple(forged.iter_rows())


def test_donor_artifact_substitution_invalidates_exact_issued_instance_before_replay(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    issued = FeatureBuilder().build_batch(
        [snapshot(0)],
        artifact_path=tmp_path / "issued.jsonl",
        maximum_rows=1,
    )
    donor = FeatureBuilder().build_batch(
        [snapshot(0, close=101.0)],
        artifact_path=tmp_path / "donor.jsonl",
        maximum_rows=1,
    )
    for field_name in (
        "artifact_path",
        "artifact_sha256",
        "content_sha256",
        "row_count",
    ):
        object.__setattr__(issued, field_name, getattr(donor, field_name))

    def artifact_was_read(*args: object, **kwargs: object) -> str:
        raise AssertionError("mutated issued batch reached donor artifact replay")

    monkeypatch.setattr(feature_builder_module, "iter_bounded_regular_lines", artifact_was_read)
    with pytest.raises(ValueError, match="metadata changed"):
        tuple(issued.iter_rows())


def test_batch_issuance_is_revoked_on_close_and_does_not_prevent_collection(tmp_path: Path) -> None:
    closed = FeatureBuilder().build_batch(
        [snapshot(0)],
        artifact_path=tmp_path / "closed.jsonl",
        maximum_rows=1,
    )
    closed.close()
    with pytest.raises(ValueError, match="exact issued FeatureBuildBatch"):
        tuple(closed.iter_rows())

    collected = FeatureBuilder().build_batch(
        [snapshot(0)],
        artifact_path=tmp_path / "collected.jsonl",
        maximum_rows=1,
    )
    reference = weakref.ref(collected)
    del collected
    gc.collect()

    assert reference() is None


def test_replay_uses_authenticated_metadata_snapshot_during_concurrent_attribute_swap(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    issued = FeatureBuilder().build_batch(
        [snapshot(0)],
        artifact_path=tmp_path / "issued.jsonl",
        maximum_rows=1,
    )
    donor = FeatureBuilder().build_batch(
        [snapshot(0, close=101.0)],
        artifact_path=tmp_path / "donor.jsonl",
        maximum_rows=1,
    )
    expected = (FeatureBuilder().update(snapshot(0)),)
    original_verify = FeatureBuildBatch.verify_issued
    verified = Event()
    resume = Event()
    paused = False

    def verify_with_barrier(candidate: object):
        nonlocal paused
        metadata = original_verify(candidate)
        if candidate is issued and not paused:
            paused = True
            verified.set()
            assert resume.wait(timeout=10)
        return metadata

    monkeypatch.setattr(FeatureBuildBatch, "verify_issued", staticmethod(verify_with_barrier))
    rows: list[FeatureRow] = []
    errors: list[BaseException] = []

    def replay() -> None:
        try:
            rows.extend(issued.iter_rows())
        except BaseException as error:
            errors.append(error)

    thread = Thread(target=replay)
    thread.start()
    assert verified.wait(timeout=10)
    for field_name in (
        "artifact_path",
        "artifact_sha256",
        "content_sha256",
        "row_count",
        "maximum_row_bytes",
        "maximum_total_bytes",
    ):
        object.__setattr__(issued, field_name, getattr(donor, field_name))
    object.__setattr__(issued, "builder_id", "forged-builder")
    object.__setattr__(issued, "builder_version", "forged-version")
    object.__setattr__(issued, "registry_id", "FR-000000000000")
    resume.set()
    thread.join(timeout=10)

    assert not thread.is_alive()
    assert errors == []
    assert rows == list(expected)


def test_replay_rejects_donor_lease_substitution_at_deterministic_barrier(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    issued = FeatureBuilder().build_batch(
        [snapshot(0)],
        artifact_path=tmp_path / "issued.jsonl",
        maximum_rows=1,
    )
    donor = FeatureBuilder().build_batch(
        [snapshot(0, close=101.0)],
        artifact_path=tmp_path / "donor.jsonl",
        maximum_rows=1,
    )
    donor_lease = FeatureBuildBatch.verify_issued(donor)
    original_verify = FeatureBuildBatch.verify_issued
    captured: list[FeatureBuildBatchLease] = []
    verified = Event()
    resume = Event()

    def verify_with_barrier(candidate: object) -> FeatureBuildBatchLease:
        lease = original_verify(candidate)
        if candidate is issued:
            captured.append(lease)
            verified.set()
            assert resume.wait(timeout=10)
        return lease

    monkeypatch.setattr(FeatureBuildBatch, "verify_issued", staticmethod(verify_with_barrier))
    rows: list[FeatureRow] = []
    errors: list[BaseException] = []

    def replay() -> None:
        try:
            rows.extend(issued.iter_rows())
        except BaseException as error:
            errors.append(error)

    thread = Thread(target=replay)
    thread.start()
    assert verified.wait(timeout=10)
    object.__setattr__(captured[0], "lease_id", donor_lease.lease_id)
    for field_name in ("artifact_path", "artifact_sha256", "content_sha256", "row_count"):
        object.__setattr__(issued, field_name, getattr(donor, field_name))
    resume.set()
    thread.join(timeout=10)

    assert not thread.is_alive()
    assert rows == []
    assert len(errors) == 1
    assert isinstance(errors[0], ValueError)
    assert "lease is no longer issued" in str(errors[0])


def test_resolved_metadata_is_detached_and_private_canonical_record_is_not_exported(
    tmp_path: Path,
) -> None:
    issued = FeatureBuilder().build_batch(
        [snapshot(0)],
        artifact_path=tmp_path / "issued.jsonl",
        maximum_rows=1,
    )
    donor = FeatureBuilder().build_batch(
        [snapshot(0, close=101.0)],
        artifact_path=tmp_path / "donor.jsonl",
        maximum_rows=1,
    )
    lease = FeatureBuildBatch.verify_issued(issued)
    metadata = FeatureBuildBatch.resolve_lease(issued, lease)

    assert type(lease) is FeatureBuildBatchLease
    assert type(metadata) is FeatureBuildBatchMetadata
    assert "_IssuedFeatureBuildBatch" not in features_package.__all__
    object.__setattr__(metadata, "artifact_path", donor.artifact_path)
    object.__setattr__(metadata, "artifact_sha256", donor.artifact_sha256)

    assert tuple(issued.iter_rows(lease)) == (FeatureBuilder().update(snapshot(0)),)


def test_close_after_replay_lease_is_acquired_keeps_active_replay_safe(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    issued = FeatureBuilder().build_batch(
        [snapshot(0)],
        artifact_path=tmp_path / "issued.jsonl",
        maximum_rows=1,
    )
    expected = (FeatureBuilder().update(snapshot(0)),)
    original_lines = feature_builder_module.iter_bounded_regular_lines
    lease_acquired = Event()
    resume = Event()

    def lines_with_barrier(*args: object, **kwargs: object):
        lease_acquired.set()
        assert resume.wait(timeout=10)
        yield from original_lines(*args, **kwargs)

    monkeypatch.setattr(feature_builder_module, "iter_bounded_regular_lines", lines_with_barrier)
    rows: list[FeatureRow] = []
    errors: list[BaseException] = []

    def replay() -> None:
        try:
            rows.extend(issued.iter_rows())
        except BaseException as error:
            errors.append(error)

    thread = Thread(target=replay)
    thread.start()
    assert lease_acquired.wait(timeout=10)
    issued.close()
    resume.set()
    thread.join(timeout=10)

    assert not thread.is_alive()
    assert errors == []
    assert rows == list(expected)
    with pytest.raises(ValueError, match="exact issued FeatureBuildBatch"):
        tuple(issued.iter_rows())


@pytest.mark.parametrize("override_kind", ("subclass", "instance"))
def test_producer_bound_batch_uses_sealed_row_construction_despite_method_overrides(
    tmp_path: Path,
    override_kind: str,
) -> None:
    snapshots = (snapshot(0), snapshot(0, symbol="ETHUSDT"))
    expected = (
        FeatureBuilder().update(snapshots[0]),
        FeatureBuilder().update(snapshots[1]),
    )
    forged_calls = 0
    overridden_reset_calls = 0

    def forged_update(snapshot: AuctionSnapshot) -> object:
        nonlocal forged_calls
        forged_calls += 1
        genuine = FeatureBuilder().update(snapshot)
        return replace(
            genuine,
            values={**genuine.values, "poc_distance_close": 999.0},
        )

    def forged_reset() -> None:
        nonlocal overridden_reset_calls
        overridden_reset_calls += 1
        raise AssertionError("overridden reset reached token-bearing construction")

    if override_kind == "subclass":

        class MaliciousFeatureBuilder(FeatureBuilder):
            def update(self, snapshot: AuctionSnapshot) -> object:
                return forged_update(snapshot)

            def reset(self) -> None:
                forged_reset()

        builder = MaliciousFeatureBuilder()
    else:
        builder = FeatureBuilder()
        builder.update = forged_update  # type: ignore[method-assign]
        builder.reset = forged_reset  # type: ignore[method-assign]

    batch = builder.build_batch(
        snapshots,
        artifact_path=tmp_path / f"{override_kind}.jsonl",
        maximum_rows=2,
    )

    assert tuple(batch.iter_rows()) == expected
    assert forged_calls == 0
    assert overridden_reset_calls == 0
    assert builder.history_size == 1


@pytest.mark.parametrize(
    ("kind", "feature_name"),
    [
        (NodeKind.HVN, "nearest_hvn_distance_close"),
        (NodeKind.LVN, "nearest_lvn_distance_close"),
    ],
)
def test_nearest_node_tie_uses_declared_representative_index_dependency(
    kind: NodeKind,
    feature_name: str,
) -> None:
    higher_index = ProfileNode(
        kind,
        99.0,
        10.0,
        1.0,
        start_index=8,
        end_index=8,
        representative_index=8,
    )
    lower_index = ProfileNode(
        kind,
        101.0,
        10.0,
        1.0,
        start_index=2,
        end_index=2,
        representative_index=2,
    )

    row = FeatureBuilder().update(snapshot(0, nodes=(higher_index, lower_index)))
    definition = next(
        item for item in builtin_feature_registry().definitions if item.name == feature_name
    )

    assert row.values[feature_name] == pytest.approx(-0.01)
    assert "snapshot.nodes.representative_index" in definition.source_fields


def snapshot(
    minute: int,
    *,
    close: float = 100.0,
    open_price: float | None = None,
    high: float | None = None,
    low: float | None = None,
    volume: float = 10.0,
    bin_volumes: dict[int, float] | None = None,
    poc: int | None = 100,
    val: int | None = 99,
    vah: int | None = 101,
    vwap: float | None = 100.0,
    location: AuctionLocation = AuctionLocation.POINT_OF_CONTROL,
    nodes: tuple[ProfileNode, ...] = (),
    event_kinds: tuple[StructuralEventKind, ...] = (),
    segment_id: int = 0,
    symbol: str = "BTCUSDT",
    timeframe: str = "1m",
) -> AuctionSnapshot:
    timestamp = BASE + timedelta(minutes=minute)
    open_value = close if open_price is None else open_price
    high_value = max(open_value, close) if high is None else high
    low_value = min(open_value, close) if low is None else low
    volumes = {100: volume} if bin_volumes is None else bin_volumes
    profile = ProfileSnapshot(
        bin_volumes=volumes,
        total_volume=sum(volumes.values()),
        poc_index=poc,
        value_area_low_index=val,
        value_area_high_index=vah,
        vwap=vwap,
        binning=BINS,
        allocation_id="fixture-allocation-v1",
    )
    candle = AuctionCandle(
        timestamp=timestamp,
        symbol=symbol,
        timeframe=timeframe,
        open=open_value,
        high=high_value,
        low=low_value,
        close=close,
        volume=volume,
        segment_id=segment_id,
    )
    events = tuple(
        StructuralEvent(
            event_id=f"event-{minute}-{kind.value}",
            timestamp=timestamp,
            kind=kind,
            payload=(),
        )
        for kind in event_kinds
    )
    return AuctionSnapshot(
        timestamp=timestamp,
        symbol=symbol,
        timeframe=timeframe,
        segment_id=segment_id,
        candle_count=1,
        latest_candle=candle,
        active_timestamps=(timestamp,),
        profile=profile,
        location=location,
        nodes=nodes,
        migration=None,
        events=events,
        window_id="rolling-bars:60",
        window_version="rolling-bars-v1:60",
        dataset_version="dataset-v1",
        config_version="config-v1",
        profile_definition_id="profile-v1",
    )


def test_builtin_registry_locks_all_audited_features_and_history_requirements() -> None:
    registry = builtin_feature_registry()
    required = {item.name: item.required_prior_observations for item in registry.definitions}

    assert registry.feature_set_id == "FS-000001"
    assert len(registry.definitions) == 26
    assert required["auction_location"] == 0
    assert required["poc_velocity_close_1"] == 1
    assert required["inside_value_rate_20"] == 19
    assert required["volume_relative_median_20"] == 19
    assert required["realized_volatility_20"] == 20
    assert required["volatility_normalized_return_20"] == 20
    assert required["return_autocorrelation_1_20"] == 20
    assert {item.family.value for item in registry.definitions} == {"auction", "sequence"}


def test_golden_current_and_one_observation_formulas() -> None:
    hvn = ProfileNode(NodeKind.HVN, 101.0, 30.0, 10.0, persistence=4)
    lvn = ProfileNode(NodeKind.LVN, 103.0, 5.0, 5.0, persistence=1)
    builder = FeatureBuilder()
    first = snapshot(
        0,
        close=100.0,
        open_price=99.0,
        high=102.0,
        low=98.0,
        volume=10.0,
        bin_volumes={98: 10.0, 99: 20.0, 100: 30.0, 101: 20.0, 102: 10.0},
    )
    second = snapshot(
        1,
        close=102.0,
        open_price=100.0,
        high=104.0,
        low=99.0,
        volume=20.0,
        bin_volumes={100: 10.0, 101: 30.0, 102: 60.0, 103: 20.0},
        poc=102,
        val=101,
        vah=103,
        vwap=101.5,
        nodes=(hvn, lvn),
    )

    first_row = builder.update(first)
    values = builder.update(second).values
    expected = json.loads(GOLDEN.read_text(encoding="utf-8"))["expected_second_values"]

    assert first_row.values["poc_velocity_close_1"] is None
    assert first_row.values["log_return_1"] is None
    assert tuple(values) == tuple(sorted(expected))
    for name, expected_value in expected.items():
        if isinstance(expected_value, float):
            assert values[name] == pytest.approx(expected_value)
        else:
            assert values[name] == expected_value


def test_history_windows_become_eligible_at_exact_observations() -> None:
    builder = FeatureBuilder()
    rows = []
    for minute in range(21):
        events = (StructuralEventKind.VALUE_REENTRY,) if minute in {0, 5} else ()
        rows.append(
            builder.update(
                snapshot(
                    minute,
                    close=100.0 + minute + (minute % 3),
                    volume=float(minute + 1),
                    location=AuctionLocation.LOWER_VALUE,
                    event_kinds=events,
                )
            )
        )

    assert rows[18].values["inside_value_rate_20"] is None
    assert rows[18].values["value_reentry_rate_20"] is None
    assert rows[18].values["volume_relative_median_20"] is None
    assert rows[19].values["inside_value_rate_20"] == 1.0
    assert rows[19].values["value_reentry_rate_20"] == pytest.approx(0.1)
    assert rows[19].values["volume_relative_median_20"] == pytest.approx(20 / 10.5 - 1)
    assert rows[19].values["realized_volatility_20"] is None
    expected_returns = [
        log((100.0 + minute + minute % 3) / (100.0 + minute - 1 + (minute - 1) % 3))
        for minute in range(1, 21)
    ]
    expected_volatility = sqrt(sum(value * value for value in expected_returns) / 20)
    assert rows[20].values["realized_volatility_20"] == pytest.approx(expected_volatility)
    assert rows[20].values["volatility_normalized_return_20"] == pytest.approx(
        expected_returns[-1] / expected_volatility
    )
    assert rows[20].values["return_autocorrelation_1_20"] is not None


def test_zero_denominators_and_undefined_references_remain_null_without_epsilon() -> None:
    builder = FeatureBuilder()
    zero = snapshot(
        0,
        close=0.0,
        volume=0.0,
        bin_volumes={},
        poc=None,
        val=None,
        vah=None,
        vwap=None,
        location=AuctionLocation.NO_VALUE,
    )
    values = builder.update(zero).values

    for name in (
        "poc_distance_close",
        "value_width_close",
        "poc_volume_share",
        "vwap_distance_close",
        "close_value_position",
        "nearest_hvn_distance_close",
        "nearest_lvn_distance_close",
        "range_close_fraction",
        "body_range_ratio",
        "upper_wick_range_ratio",
        "lower_wick_range_ratio",
    ):
        assert values[name] is None
    assert values["max_node_persistence_bars"] == 0
    assert values["auction_location"] == "no_value"


def test_zero_trailing_volatility_and_volume_baseline_remain_null() -> None:
    builder = FeatureBuilder()
    rows = [builder.update(snapshot(minute, close=100.0, volume=0.0)) for minute in range(21)]

    assert rows[-1].values["realized_volatility_20"] == 0.0
    assert rows[-1].values["volatility_normalized_return_20"] is None
    assert rows[-1].values["return_autocorrelation_1_20"] is None
    assert rows[-1].values["volume_relative_median_20"] is None
    assert rows[-1].values["log_volume_ratio_1"] is None


@pytest.mark.parametrize(
    "kind",
    [
        StructuralEventKind.WINDOW_RESET,
        StructuralEventKind.GAP_RESET,
        StructuralEventKind.SEGMENT_RESET,
    ],
)
def test_explicit_reset_events_clear_history_before_current_row(
    kind: StructuralEventKind,
) -> None:
    builder = FeatureBuilder()
    builder.update(snapshot(0, close=100.0))
    reset_row = builder.update(
        snapshot(
            2,
            close=110.0,
            event_kinds=(kind,),
            segment_id=int(kind is StructuralEventKind.SEGMENT_RESET),
        )
    )

    assert reset_row.values["log_return_1"] is None
    assert reset_row.values["poc_velocity_close_1"] is None
    assert reset_row.values["location_dwell_bars"] == 1
    assert builder.history_size == 1


def test_segment_change_without_event_is_a_boundary_but_rolling_eviction_is_not() -> None:
    builder = FeatureBuilder()
    first = builder.update(snapshot(0, close=100.0))
    second = builder.update(snapshot(1, close=101.0))
    reset = builder.update(snapshot(2, close=102.0, segment_id=1))

    assert first.values["location_dwell_bars"] == 1
    assert second.values["location_dwell_bars"] == 2
    assert second.values["log_return_1"] is not None
    assert reset.values["location_dwell_bars"] == 1
    assert reset.values["log_return_1"] is None


def test_order_series_and_unexplained_gap_fail_atomically() -> None:
    builder = FeatureBuilder()
    first = builder.update(snapshot(0))
    for invalid, message in (
        (snapshot(0), "duplicate"),
        (snapshot(-1), "out-of-order"),
        (snapshot(1, symbol="ETHUSDT"), "symbol"),
        (snapshot(1, timeframe="5m"), "timeframe"),
        (snapshot(2), "gap"),
    ):
        with pytest.raises(ValueError, match=message):
            builder.update(invalid)
        assert builder.history_size == 1
    valid = builder.update(snapshot(1, close=101.0))
    assert valid.values["log_return_1"] == pytest.approx(log(1.01))
    assert first.values["log_return_1"] is None


@pytest.mark.parametrize(
    "field",
    ("dataset_version", "config_version", "profile_definition_id", "window_version"),
)
def test_stream_identity_drift_cannot_reuse_trailing_history(field: str) -> None:
    builder = FeatureBuilder()
    first = snapshot(0)
    builder.update(first)

    with pytest.raises(ValueError, match=field):
        builder.update(replace(snapshot(1), **{field: "drifted-v2"}))

    assert builder.history_size == 1
    assert builder.update(snapshot(1, close=101.0)).values["log_return_1"] is not None


def test_prefix_invariance_future_poisoning_and_bounded_history() -> None:
    prefix = tuple(snapshot(minute, close=100.0 + minute) for minute in range(30))
    first = FeatureBuilder().build(prefix)
    poisoned = FeatureBuilder().build(
        (*prefix, snapshot(30, close=1_000_000.0, volume=1_000_000.0))
    )
    bounded = FeatureBuilder()
    bounded.build(tuple(snapshot(minute, close=100.0 + minute) for minute in range(100)))

    assert tuple(row.canonical_json() for row in poisoned[: len(prefix)]) == tuple(
        row.canonical_json() for row in first
    )
    assert bounded.history_size == 21


def test_builder_rejects_metadata_drift_under_reused_feature_set_id() -> None:
    changed = list(BUILTIN_DEFINITIONS)
    original = changed[0]
    changed[0] = replace(original, definition="Changed formula metadata.")
    drifted = FeatureRegistry("FS-000001", changed)

    with pytest.raises(ValueError, match="exactly match"):
        FeatureBuilder(drifted)
