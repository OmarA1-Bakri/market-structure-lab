from __future__ import annotations

import copy
import hashlib
import itertools
import json
import subprocess
import sys
from collections.abc import Iterator
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Event, Thread

import polars as pl
import pytest
import market_structure_lab.data.derived as derived_data
import market_structure_lab.features.builder as feature_builder_module

from market_structure_lab.auction.engine import AuctionLocation, AuctionSnapshot
from market_structure_lab.auction.models import AuctionCandle
from market_structure_lab.data.derived import (
    LEAKAGE_AUDIT_NAME,
    DerivedPublicationIdentity,
    FeatureLeakageFieldEvidence,
    LeakageAuditApproval,
    LeakageNegativePattern,
    PublicationBusyError,
    PublicationCleanupError,
    feature_partition_records,
    publish_feature_rows as publish_feature_batch,
    publish_market_events,
    read_derived_manifest,
    read_leakage_audit_receipt,
    verify_derived_publication,
)
from market_structure_lab.events.models import EventKind, make_event
from market_structure_lab.features.builder import FeatureBuildBatch, FeatureBuilder
from market_structure_lab.features.builtin import builtin_feature_registry
from market_structure_lab.features.models import FeatureRow
from market_structure_lab.features.registry import (
    FeatureRegistry,
    NormalizationRequirement,
    ObservableCutoffRule,
)
from market_structure_lab.profiles.binning import FixedStepBins
from market_structure_lab.profiles.models import ProfileSnapshot


def test_feature_and_derived_modules_import_in_a_fresh_interpreter() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "from market_structure_lab.features import builtin_feature_registry; "
                "from market_structure_lab.events import make_event, segment_fixed_windows; "
                "from market_structure_lab.data.derived import DerivedPublicationIdentity; "
                "assert len(builtin_feature_registry().definitions) == 26; "
                "assert callable(make_event) and callable(segment_fixed_windows); "
                "assert DerivedPublicationIdentity.__name__ == 'DerivedPublicationIdentity'"
            ),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr


def registry() -> FeatureRegistry:
    return builtin_feature_registry()


def leakage_approval(active: FeatureRegistry) -> LeakageAuditApproval:
    return LeakageAuditApproval(
        schema_version=1,
        feature_registry_sha256=active.sha256,
        reviewer_id="independent-reviewer-v1",
        review_artifact_sha256=hashlib.sha256(b"independent manual review").hexdigest(),
        field_test_evidence=tuple(
            (name, hashlib.sha256(f"cutoff dependency test:{name}".encode()).hexdigest())
            for name in active.names
        ),
        negative_test_evidence=tuple(
            (
                pattern,
                hashlib.sha256(f"negative test:{pattern.value}".encode()).hexdigest(),
            )
            for pattern in LeakageNegativePattern
        ),
    )


_BATCH_SEQUENCE = itertools.count()


def publish_feature_rows(
    snapshots,
    *,
    output_root,
    identity,
    registry,
    leakage_audit,
    max_rows_per_part=100_000,
):
    batch_root = Path(output_root) / ".test-producer-batches"
    batch_root.mkdir(parents=True, exist_ok=True)
    batch = FeatureBuilder(registry).build_batch(
        snapshots,
        artifact_path=batch_root / f"batch-{next(_BATCH_SEQUENCE):06d}.jsonl",
        maximum_rows=10_000,
    )
    return publish_feature_batch(
        batch,
        output_root=output_root,
        identity=identity,
        registry=registry,
        leakage_audit=leakage_audit,
        max_rows_per_part=max_rows_per_part,
    )


def build_feature_batch(
    snapshots,
    *,
    artifact_path: Path,
    maximum_rows: int,
) -> FeatureBuildBatch:
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    return FeatureBuilder().build_batch(
        snapshots,
        artifact_path=artifact_path,
        maximum_rows=maximum_rows,
    )


def feature_batch_constructor_fields(batch: FeatureBuildBatch) -> dict[str, object]:
    return {
        "artifact_path": batch.artifact_path,
        "artifact_sha256": batch.artifact_sha256,
        "content_sha256": batch.content_sha256,
        "row_count": batch.row_count,
        "builder_id": batch.builder_id,
        "builder_version": batch.builder_version,
        "feature_registry_sha256": batch.feature_registry_sha256,
        "dependency_contract_sha256": batch.dependency_contract_sha256,
        "registry_id": batch.registry_id,
        "maximum_row_bytes": batch.maximum_row_bytes,
    }


def identity(
    active_registry: FeatureRegistry | None = None,
    *,
    normalizer_artifact_sha256: str | None = "c" * 64,
) -> DerivedPublicationIdentity:
    active = active_registry or registry()
    approval = leakage_approval(active)
    return DerivedPublicationIdentity(
        dataset_version="DS-000009",
        dataset_snapshot_sha256="a" * 64,
        feature_set_id=active.feature_set_id,
        feature_registry_sha256=active.sha256,
        config_version="config-v1",
        profile_version="profile-v1",
        window_policy_id="rolling-20",
        event_version="event-v1",
        normalizer_artifact_sha256=normalizer_artifact_sha256,
        leakage_audit_approval_sha256=approval.sha256,
        code_commit="0123456789abcdef",
        uv_lock_sha256="b" * 64,
    )


def test_feature_publication_emits_checksum_bound_independent_leakage_receipt(
    tmp_path: Path,
) -> None:
    active = registry()
    approval = leakage_approval(active)
    frozen = identity(active)
    manifest = publish_feature_rows(
        [feature_snapshot(0)],
        output_root=tmp_path,
        identity=frozen,
        registry=active,
        leakage_audit=approval,
    )
    published = tmp_path / "dataset_version=DS-000009" / "feature_set=FS-000001" / "features"
    receipt = read_leakage_audit_receipt(published / LEAKAGE_AUDIT_NAME)

    assert manifest.leakage_audit_receipt_sha256 == receipt.receipt_sha256
    assert manifest.schema_version == 3
    assert receipt.schema_version == 2
    assert receipt.feature_registry_sha256 == active.sha256
    assert (
        receipt.dependency_contract_sha256
        == manifest.feature_dependency_contract_sha256
        == active.dependency_contract_sha256
    )
    assert receipt.approval_sha256 == approval.sha256
    assert receipt.publication_identity_sha256 == frozen.sha256
    assert receipt.builder_output_sha256 == manifest.feature_builder_output_sha256
    assert receipt.published_content_sha256 == manifest.content_sha256
    assert receipt.builder_output_row_count == manifest.row_count
    assert tuple(item.feature_name for item in receipt.fields) == active.names
    assert tuple(item.builder_id for item in receipt.fields) == tuple(
        definition.builder_id for definition in active.definitions
    )
    assert tuple(item.dependency_contract_sha256 for item in receipt.fields) == tuple(
        definition.dependency_contract_sha256 for definition in active.definitions
    )
    assert receipt.residual_manual_review_required is True
    verify_derived_publication(published, manifest)


def test_plain_feature_rows_are_rejected_without_a_producer_envelope(tmp_path: Path) -> None:
    active = registry()
    with pytest.raises(TypeError, match="producer-bound FeatureBuildBatch"):
        publish_feature_batch(
            [feature_row(0)],  # type: ignore[arg-type]
            output_root=tmp_path,
            identity=identity(active),
            registry=active,
            leakage_audit=leakage_approval(active),
        )
    assert not (tmp_path / "dataset_version=DS-000009").exists()


def test_production_feature_builder_exposes_no_row_binding_test_bypass() -> None:
    assert not hasattr(feature_builder_module, "_bind_feature_rows_for_test")
    assert not any(
        "bind" in name and "row" in name
        for name in vars(feature_builder_module)
        if name != "dataclass_field"
    )


@pytest.mark.parametrize("override_kind", ("subclass", "instance"))
def test_feature_publication_ignores_overridden_builder_update(
    tmp_path: Path,
    override_kind: str,
) -> None:
    active = registry()
    snapshot = feature_snapshot(0)
    expected = FeatureBuilder().update(snapshot)
    forged_calls = 0

    def forged_update(snapshot: AuctionSnapshot) -> object:
        nonlocal forged_calls
        forged_calls += 1
        genuine = FeatureBuilder().update(snapshot)
        return replace(
            genuine,
            values={**genuine.values, "poc_distance_close": 999.0},
        )

    if override_kind == "subclass":

        class MaliciousFeatureBuilder(FeatureBuilder):
            def update(self, snapshot: AuctionSnapshot) -> object:
                return forged_update(snapshot)

        builder = MaliciousFeatureBuilder()
    else:
        builder = FeatureBuilder()
        builder.update = forged_update  # type: ignore[method-assign]

    batch_root = tmp_path / "batch"
    batch_root.mkdir()
    batch = builder.build_batch(
        [snapshot],
        artifact_path=batch_root / "rows.jsonl",
        maximum_rows=1,
    )
    manifest = publish_feature_batch(
        batch,
        output_root=tmp_path,
        identity=identity(active),
        registry=active,
        leakage_audit=leakage_approval(active),
    )
    published = tmp_path / "dataset_version=DS-000009" / "feature_set=FS-000001" / "features"
    frame = pl.read_parquet(published / manifest.partitions[0].path)

    assert frame["poc_distance_close"].to_list() == [expected.values["poc_distance_close"]]
    assert forged_calls == 0


@pytest.mark.parametrize(
    "forgery_kind",
    ("replace", "copy", "deepcopy", "manual", "subclass", "donor", "overridden_build_batch"),
)
def test_feature_publication_rejects_every_nonexact_or_mutated_batch_before_replay(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    forgery_kind: str,
) -> None:
    issued_batches: list[FeatureBuildBatch] = []
    (tmp_path / "batch").mkdir()
    if forgery_kind == "overridden_build_batch":

        class MaliciousFeatureBuilder(FeatureBuilder):
            def build_batch(self, snapshots, *, artifact_path, maximum_rows):
                issued = super().build_batch(
                    snapshots,
                    artifact_path=artifact_path,
                    maximum_rows=maximum_rows,
                )
                issued_batches.append(issued)
                return replace(issued)

        forged = MaliciousFeatureBuilder().build_batch(
            [feature_snapshot(0)],
            artifact_path=tmp_path / "batch" / "override.jsonl",
            maximum_rows=1,
        )
    else:
        issued = build_feature_batch(
            [feature_snapshot(0)],
            artifact_path=tmp_path / "batch" / "issued.jsonl",
            maximum_rows=1,
        )
        issued_batches.append(issued)
        fields = feature_batch_constructor_fields(issued)
        if forgery_kind == "replace":
            forged = replace(issued)
        elif forgery_kind == "copy":
            forged = copy.copy(issued)
        elif forgery_kind == "deepcopy":
            forged = copy.deepcopy(issued)
        elif forgery_kind == "manual":
            forged = FeatureBuildBatch(**fields)
        elif forgery_kind == "subclass":

            class ForgedFeatureBuildBatch(FeatureBuildBatch):
                pass

            forged = ForgedFeatureBuildBatch(**fields)
        else:
            donor = build_feature_batch(
                [feature_snapshot(0, value=0.2)],
                artifact_path=tmp_path / "batch" / "donor.jsonl",
                maximum_rows=1,
            )
            issued_batches.append(donor)
            for field_name in (
                "artifact_path",
                "artifact_sha256",
                "content_sha256",
                "row_count",
            ):
                object.__setattr__(issued, field_name, getattr(donor, field_name))
            forged = issued

    replayed = 0

    def rows(self, metadata=None) -> Iterator[FeatureRow]:
        nonlocal replayed
        replayed += 1
        raise AssertionError("unissued or mutated batch reached publication replay")
        yield

    monkeypatch.setattr(FeatureBuildBatch, "iter_rows", rows)
    active = registry()
    with pytest.raises((TypeError, ValueError), match="issued"):
        publish_feature_batch(
            forged,
            output_root=tmp_path,
            identity=identity(active),
            registry=active,
            leakage_audit=leakage_approval(active),
        )

    assert replayed == 0
    assert not (tmp_path / "dataset_version=DS-000009").exists()
    for batch in issued_batches:
        batch.close()


def test_publication_uses_authenticated_metadata_snapshot_during_concurrent_swap(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    active = registry()
    issued = build_feature_batch(
        [feature_snapshot(0)],
        artifact_path=tmp_path / "batch" / "issued.jsonl",
        maximum_rows=1,
    )
    donor = build_feature_batch(
        [feature_snapshot(0, value=0.2)],
        artifact_path=tmp_path / "batch" / "donor.jsonl",
        maximum_rows=1,
    )
    expected = FeatureBuilder().update(feature_snapshot(0))
    expected_output_sha256 = issued.content_sha256
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
    manifests = []
    errors: list[BaseException] = []

    def publish() -> None:
        try:
            manifests.append(
                publish_feature_batch(
                    issued,
                    output_root=tmp_path,
                    identity=identity(active),
                    registry=active,
                    leakage_audit=leakage_approval(active),
                )
            )
        except BaseException as error:
            errors.append(error)

    thread = Thread(target=publish)
    thread.start()
    assert verified.wait(timeout=10)
    for field_name in (
        "artifact_path",
        "artifact_sha256",
        "content_sha256",
        "row_count",
        "maximum_row_bytes",
    ):
        object.__setattr__(issued, field_name, getattr(donor, field_name))
    object.__setattr__(issued, "builder_id", "forged-builder")
    object.__setattr__(issued, "builder_version", "forged-version")
    object.__setattr__(issued, "registry_id", "FR-000000000000")
    resume.set()
    thread.join(timeout=10)

    assert not thread.is_alive()
    assert errors == []
    assert len(manifests) == 1
    manifest = manifests[0]
    published = tmp_path / "dataset_version=DS-000009" / "feature_set=FS-000001" / "features"
    receipt = read_leakage_audit_receipt(published / LEAKAGE_AUDIT_NAME)
    frame = pl.read_parquet(published / manifest.partitions[0].path)
    assert manifest.feature_builder_output_sha256 == expected_output_sha256
    assert receipt.builder_output_sha256 == expected_output_sha256
    assert receipt.builder_output_row_count == 1
    assert frame["poc_distance_close"].to_list() == [expected.values["poc_distance_close"]]


def test_publication_rejects_donor_lease_substitution_at_deterministic_barrier(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    active = registry()
    issued = build_feature_batch(
        [feature_snapshot(0)],
        artifact_path=tmp_path / "batch" / "issued.jsonl",
        maximum_rows=1,
    )
    donor = build_feature_batch(
        [feature_snapshot(0, value=0.2)],
        artifact_path=tmp_path / "batch" / "donor.jsonl",
        maximum_rows=1,
    )
    donor_lease = FeatureBuildBatch.verify_issued(donor)
    original_verify = FeatureBuildBatch.verify_issued
    captured: list[object] = []
    verified = Event()
    resume = Event()

    def verify_with_barrier(candidate: object):
        lease = original_verify(candidate)
        if candidate is issued:
            captured.append(lease)
            verified.set()
            assert resume.wait(timeout=10)
        return lease

    monkeypatch.setattr(FeatureBuildBatch, "verify_issued", staticmethod(verify_with_barrier))
    errors: list[BaseException] = []

    def publish() -> None:
        try:
            publish_feature_batch(
                issued,
                output_root=tmp_path,
                identity=identity(active),
                registry=active,
                leakage_audit=leakage_approval(active),
            )
        except BaseException as error:
            errors.append(error)

    thread = Thread(target=publish)
    thread.start()
    assert verified.wait(timeout=10)
    object.__setattr__(captured[0], "lease_id", donor_lease.lease_id)
    for field_name in ("artifact_path", "artifact_sha256", "content_sha256", "row_count"):
        object.__setattr__(issued, field_name, getattr(donor, field_name))
    resume.set()
    thread.join(timeout=10)

    assert not thread.is_alive()
    assert len(errors) == 1
    assert isinstance(errors[0], ValueError)
    assert "lease is no longer issued" in str(errors[0])
    final = tmp_path / "dataset_version=DS-000009" / "feature_set=FS-000001" / "features"
    assert not final.exists()


def test_publication_uses_one_detached_registry_snapshot_during_live_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    active = registry()
    approval = leakage_approval(active)
    frozen_identity = identity(active)
    issued = build_feature_batch(
        [feature_snapshot(0)],
        artifact_path=tmp_path / "batch" / "issued.jsonl",
        maximum_rows=1,
    )
    original_snapshot = FeatureRegistry.snapshot
    snapshotted = Event()
    resume = Event()

    def snapshot_with_barrier(candidate: FeatureRegistry):
        snapshot = original_snapshot(candidate)
        if candidate is active:
            snapshotted.set()
            assert resume.wait(timeout=10)
        return snapshot

    monkeypatch.setattr(FeatureRegistry, "snapshot", snapshot_with_barrier)
    manifests = []
    errors: list[BaseException] = []

    def publish() -> None:
        try:
            manifests.append(
                publish_feature_batch(
                    issued,
                    output_root=tmp_path,
                    identity=frozen_identity,
                    registry=active,
                    leakage_audit=approval,
                )
            )
        except BaseException as error:
            errors.append(error)

    thread = Thread(target=publish)
    thread.start()
    assert snapshotted.wait(timeout=10)
    poisoned = replace(active._definitions[0], source_fields=("snapshot.future.close",))
    active._definitions = (poisoned, *active._definitions[1:])
    active._names = tuple(reversed(active._names))
    resume.set()
    thread.join(timeout=10)

    assert not thread.is_alive()
    assert errors == []
    assert len(manifests) == 1
    manifest = manifests[0]
    published = tmp_path / "dataset_version=DS-000009" / "feature_set=FS-000001" / "features"
    receipt = read_leakage_audit_receipt(published / LEAKAGE_AUDIT_NAME)
    assert manifest.feature_dependency_contract_sha256 == receipt.dependency_contract_sha256
    assert all("future" not in source for item in receipt.fields for source in item.source_fields)
    verify_derived_publication(published, manifest)


def test_future_source_receipt_field_is_semantically_rejected(tmp_path: Path) -> None:
    active = registry()
    manifest = publish_feature_rows(
        [feature_snapshot(0)],
        output_root=tmp_path,
        identity=identity(active),
        registry=active,
        leakage_audit=leakage_approval(active),
    )
    published = tmp_path / "dataset_version=DS-000009" / "feature_set=FS-000001" / "features"
    receipt = read_leakage_audit_receipt(published / LEAKAGE_AUDIT_NAME)
    field: FeatureLeakageFieldEvidence = receipt.fields[0]

    with pytest.raises(ValueError, match="future or outcome source"):
        replace(
            field,
            source_fields=("snapshot.future.close",),
            dependency_contract_sha256="0" * 64,
        )
    assert manifest.feature_dependency_contract_sha256 == active.dependency_contract_sha256


def test_verifier_rejects_manifest_dependency_digest_not_bound_to_receipt(
    tmp_path: Path,
) -> None:
    active = registry()
    manifest = publish_feature_rows(
        [feature_snapshot(0)],
        output_root=tmp_path,
        identity=identity(active),
        registry=active,
        leakage_audit=leakage_approval(active),
    )
    published = tmp_path / "dataset_version=DS-000009" / "feature_set=FS-000001" / "features"
    provisional = replace(
        manifest,
        feature_dependency_contract_sha256="0" * 64,
        publication_sha256="0" * 64,
    )
    tampered = replace(
        provisional,
        publication_sha256=hashlib.sha256(
            derived_data._canonical_json(provisional.logical_dict())
        ).hexdigest(),
    )
    (published / derived_data.MANIFEST_NAME).write_text(tampered.to_json(), encoding="utf-8")
    (published / derived_data.SUCCESS_NAME).write_text(
        tampered.publication_sha256 + "\n", encoding="utf-8"
    )

    with pytest.raises(ValueError, match="not bound to the publication"):
        verify_derived_publication(published)


def test_close_after_publication_authentication_but_before_replay_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    active = registry()
    issued = build_feature_batch(
        [feature_snapshot(0)],
        artifact_path=tmp_path / "batch" / "issued.jsonl",
        maximum_rows=1,
    )
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
    errors: list[BaseException] = []

    def publish() -> None:
        try:
            publish_feature_batch(
                issued,
                output_root=tmp_path,
                identity=identity(active),
                registry=active,
                leakage_audit=leakage_approval(active),
            )
        except BaseException as error:
            errors.append(error)

    thread = Thread(target=publish)
    thread.start()
    assert verified.wait(timeout=10)
    issued.close()
    resume.set()
    thread.join(timeout=10)

    assert not thread.is_alive()
    assert len(errors) == 1
    assert isinstance(errors[0], ValueError)
    assert "issued for this FeatureBuildBatch" in str(errors[0])
    final = tmp_path / "dataset_version=DS-000009" / "feature_set=FS-000001" / "features"
    assert not final.exists()


def test_leakage_approval_metadata_caps_and_negative_evidence_are_exact() -> None:
    active = registry()
    approval = leakage_approval(active)

    exact = replace(approval, reviewer_id="r" * 127)
    assert len(exact.reviewer_id) == 127
    with pytest.raises(ValueError, match="reviewer_id is too long"):
        replace(approval, reviewer_id="r" * 128)
    with pytest.raises(ValueError, match="every required leakage pattern"):
        replace(approval, negative_test_evidence=approval.negative_test_evidence[:-1])


def test_mismatched_producer_digest_never_becomes_a_durable_publication(
    tmp_path: Path,
) -> None:
    active = registry()
    batch = build_feature_batch(
        [feature_snapshot(0)],
        artifact_path=tmp_path / "batch" / "rows.jsonl",
        maximum_rows=1,
    )
    object.__setattr__(batch, "content_sha256", "0" * 64)

    with pytest.raises(ValueError, match="metadata changed"):
        publish_feature_batch(
            batch,
            output_root=tmp_path,
            identity=identity(active),
            registry=active,
            leakage_audit=leakage_approval(active),
        )
    final = tmp_path / "dataset_version=DS-000009" / "feature_set=FS-000001" / "features"
    assert not final.exists()


def test_receipt_size_preflight_and_atomic_writer_enforce_exact_byte_cap(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    active = registry()
    batch = build_feature_batch(
        [feature_snapshot(0)],
        artifact_path=tmp_path / "batch" / "rows.jsonl",
        maximum_rows=1,
    )
    consumed = 0

    def exploding(self, metadata=None) -> Iterator[FeatureRow]:
        nonlocal consumed
        consumed += 1
        raise AssertionError("batch replay occurred before receipt size preflight")
        yield

    monkeypatch.setattr(FeatureBuildBatch, "iter_rows", exploding)
    monkeypatch.setattr(derived_data, "_MAX_LEAKAGE_AUDIT_BYTES", 1)
    with pytest.raises(ValueError, match="receipt exceeds"):
        publish_feature_batch(
            batch,
            output_root=tmp_path,
            identity=identity(active),
            registry=active,
            leakage_audit=leakage_approval(active),
        )
    assert consumed == 0

    exact = tmp_path / "exact.txt"
    derived_data._atomic_write_text(exact, "é", maximum_bytes=2)
    assert exact.read_bytes() == "é".encode()
    oversized = tmp_path / "oversized.txt"
    with pytest.raises(ValueError, match="byte limit"):
        derived_data._atomic_write_text(oversized, "é", maximum_bytes=1)
    assert not oversized.exists()
    assert not (tmp_path / ".oversized.txt.tmp").exists()


def test_leakage_approval_is_validated_before_feature_rows_are_iterated(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    active = registry()
    approval = leakage_approval(active)
    batch = build_feature_batch(
        [feature_snapshot(0)],
        artifact_path=tmp_path / "batch" / "rows.jsonl",
        maximum_rows=1,
    )
    consumed = 0

    def rows(self, metadata=None) -> Iterator[FeatureRow]:
        nonlocal consumed
        consumed += 1
        yield feature_row(0)

    monkeypatch.setattr(FeatureBuildBatch, "iter_rows", rows)

    with pytest.raises(ValueError, match="bind the leakage audit approval"):
        publish_feature_batch(
            batch,
            output_root=tmp_path,
            identity=replace(
                identity(active),
                leakage_audit_approval_sha256="0" * 64,
            ),
            registry=active,
            leakage_audit=approval,
        )
    assert consumed == 0


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"source_fields": ("snapshot.future.close",)}, "future or outcome source"),
        (
            {"normalization_requirement": NormalizationRequirement.FULL_PERIOD},
            "full-period normalization",
        ),
        (
            {"normalization_requirement": NormalizationRequirement.GLOBAL_MIN_MAX},
            "global min/max normalization",
        ),
        (
            {"observable_cutoff_rule": ObservableCutoffRule.CENTERED_WINDOW},
            "centered window",
        ),
        (
            {"observable_cutoff_rule": ObservableCutoffRule.FUTURE_DEPENDENT_LABEL},
            "future-dependent label",
        ),
        (
            {"observable_cutoff_rule": ObservableCutoffRule.AFTER_DECLARED_CUTOFF},
            "after declared cutoff",
        ),
    ],
)
def test_semantic_leakage_is_rejected_before_publication_or_row_iteration(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    changes: dict[str, object],
    message: str,
) -> None:
    active = registry()
    frozen = identity(active)
    approval = leakage_approval(active)
    batch = build_feature_batch(
        [feature_snapshot(0)],
        artifact_path=tmp_path / "batch" / "rows.jsonl",
        maximum_rows=1,
    )
    poisoned = replace(active.definitions[0])
    for field_name, value in changes.items():
        object.__setattr__(poisoned, field_name, value)
    object.__setattr__(active, "_definitions", (poisoned, *active.definitions[1:]))
    consumed = 0

    def rows(self, metadata=None) -> Iterator[FeatureRow]:
        nonlocal consumed
        consumed += 1
        yield feature_row(0)

    monkeypatch.setattr(FeatureBuildBatch, "iter_rows", rows)

    with pytest.raises(ValueError, match=message):
        publish_feature_batch(
            batch,
            output_root=tmp_path,
            identity=frozen,
            registry=active,
            leakage_audit=approval,
        )
    assert consumed == 0


def test_leakage_receipt_tampering_and_symlinks_fail_verification(tmp_path: Path) -> None:
    active = registry()
    manifest = publish_feature_rows(
        [feature_snapshot(0)],
        output_root=tmp_path,
        identity=identity(active),
        registry=active,
        leakage_audit=leakage_approval(active),
    )
    published = tmp_path / "dataset_version=DS-000009" / "feature_set=FS-000001" / "features"
    receipt_path = published / LEAKAGE_AUDIT_NAME
    original = receipt_path.read_bytes()

    receipt_path.write_bytes(original + b"tamper")
    with pytest.raises(ValueError, match="leakage audit artifact checksum"):
        verify_derived_publication(published, manifest)

    receipt_path.write_bytes(original)
    outside = tmp_path / "outside-receipt.json"
    outside.write_bytes(original)
    receipt_path.unlink()
    receipt_path.symlink_to(outside)
    with pytest.raises(RuntimeError, match="symlink"):
        verify_derived_publication(published, manifest)


def test_verifier_uses_canonical_disk_manifest_even_when_supplied_object_is_valid(
    tmp_path: Path,
) -> None:
    active = registry()
    manifest = publish_feature_rows(
        [feature_snapshot(0)],
        output_root=tmp_path,
        identity=identity(active),
        registry=active,
        leakage_audit=leakage_approval(active),
    )
    published = tmp_path / "dataset_version=DS-000009" / "feature_set=FS-000001" / "features"
    (published / "manifest.json").write_text("{}\n", encoding="utf-8")

    with pytest.raises(ValueError, match="invalid derived publication manifest"):
        verify_derived_publication(published, manifest)


def test_verifier_rejects_a_supplied_manifest_that_differs_from_disk(tmp_path: Path) -> None:
    active = registry()
    manifest = publish_feature_rows(
        [feature_snapshot(0)],
        output_root=tmp_path,
        identity=identity(active),
        registry=active,
        leakage_audit=leakage_approval(active),
    )
    published = tmp_path / "dataset_version=DS-000009" / "feature_set=FS-000001" / "features"

    with pytest.raises(ValueError, match="does not exactly match"):
        verify_derived_publication(
            published,
            replace(manifest, max_rows_per_part=manifest.max_rows_per_part + 1),
        )


def feature_snapshot(
    minute: int,
    *,
    symbol: str = "BTCUSDT",
    value: float | None = 0.1,
    timeframe: str = "1m",
    segment_id: int = 0,
) -> AuctionSnapshot:
    timestamp = datetime(2025, 1, 1, tzinfo=UTC) + timedelta(minutes=minute)
    poc_index = None if value is None else 100 + round(value * 10)
    volumes = {} if poc_index is None else {poc_index: 10.0}
    profile = ProfileSnapshot(
        bin_volumes=volumes,
        total_volume=sum(volumes.values()),
        poc_index=poc_index,
        value_area_low_index=poc_index,
        value_area_high_index=poc_index,
        vwap=None if poc_index is None else float(poc_index),
        binning=FixedStepBins(step=1.0),
        allocation_id="fixture-allocation-v1",
    )
    candle = AuctionCandle(
        timestamp=timestamp,
        symbol=symbol,
        timeframe=timeframe,
        open=100.0,
        high=101.0,
        low=99.0,
        close=100.0,
        volume=10.0,
        segment_id=segment_id,
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
        location=(
            AuctionLocation.NO_VALUE if poc_index is None else AuctionLocation.POINT_OF_CONTROL
        ),
        nodes=(),
        migration=None,
        events=(),
        window_id="rolling-20",
        window_version="rolling-20",
        dataset_version="DS-000009",
        config_version="config-v1",
        profile_definition_id="profile-v1",
    )


def feature_row(
    minute: int,
    *,
    symbol: str = "BTCUSDT",
    value: float | None = 0.1,
    timeframe: str = "1m",
    segment_id: int = 0,
) -> FeatureRow:
    return FeatureBuilder().update(
        feature_snapshot(
            minute,
            symbol=symbol,
            value=value,
            timeframe=timeframe,
            segment_id=segment_id,
        )
    )


def test_feature_publication_has_fixed_chunks_and_deterministic_paths(tmp_path: Path) -> None:
    active = registry()
    frozen = identity(active)
    snapshots = [feature_snapshot(index) for index in range(5)]

    first = publish_feature_rows(
        snapshots,
        output_root=tmp_path / "first",
        identity=frozen,
        registry=active,
        leakage_audit=leakage_approval(active),
        max_rows_per_part=2,
    )
    second = publish_feature_rows(
        iter(snapshots),
        output_root=tmp_path / "second",
        identity=frozen,
        registry=active,
        leakage_audit=leakage_approval(active),
        max_rows_per_part=2,
    )

    assert first == second
    assert [item.row_count for item in first.partitions] == [2, 2, 1]
    assert first.max_buffered_rows == 2
    assert first.row_count == 5
    assert first.partitions[0].path == (
        "symbol=BTCUSDT/timeframe=1m/year=2025/month=01/part-000001.parquet"
    )
    published = (
        tmp_path / "first" / "dataset_version=DS-000009" / "feature_set=FS-000001" / "features"
    )
    assert (published / "_SUCCESS").is_file()
    verify_derived_publication(published)


def test_feature_publication_requires_normalizer_identity_before_rows_are_read(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    active = registry()
    batch = build_feature_batch(
        [feature_snapshot(0)],
        artifact_path=tmp_path / "batch" / "rows.jsonl",
        maximum_rows=1,
    )

    def exploding_rows(self, metadata=None) -> Iterator[FeatureRow]:
        raise AssertionError("feature rows were read before provenance validation")
        yield

    monkeypatch.setattr(FeatureBuildBatch, "iter_rows", exploding_rows)

    with pytest.raises(ValueError, match="normalizer"):
        publish_feature_batch(
            batch,
            output_root=tmp_path,
            identity=identity(active, normalizer_artifact_sha256=None),
            registry=active,
            leakage_audit=leakage_approval(active),
        )


def test_feature_columns_are_declared_columns_not_a_mapping_blob(tmp_path: Path) -> None:
    active = registry()
    manifest = publish_feature_rows(
        [feature_snapshot(0, value=None)],
        output_root=tmp_path,
        identity=identity(active),
        registry=active,
        leakage_audit=leakage_approval(active),
    )
    published = tmp_path / "dataset_version=DS-000009" / "feature_set=FS-000001" / "features"
    frame = pl.read_parquet(published / manifest.partitions[0].path)

    assert "values" not in frame.columns
    assert frame["auction_location"].to_list() == ["no_value"]
    assert frame["log_return_1"].to_list() == [None]
    assert manifest.evidence.null_value_count > 1
    assert manifest.evidence.warmup_row_count == 1


def test_partition_numbering_remains_unique_when_a_month_reappears(tmp_path: Path) -> None:
    active = registry()
    manifest = publish_feature_rows(
        [
            feature_snapshot(0, timeframe="1h"),
            feature_snapshot(44_640, timeframe="1h", segment_id=1),
            feature_snapshot(0, timeframe="1m"),
        ],
        output_root=tmp_path,
        identity=identity(active),
        registry=active,
        leakage_audit=leakage_approval(active),
        max_rows_per_part=1,
    )

    paths = tuple(item.path for item in manifest.partitions)
    assert paths == (
        "symbol=BTCUSDT/timeframe=1h/year=2025/month=01/part-000001.parquet",
        "symbol=BTCUSDT/timeframe=1h/year=2025/month=02/part-000001.parquet",
        "symbol=BTCUSDT/timeframe=1m/year=2025/month=01/part-000001.parquet",
    )


def test_feature_partition_binding_is_timeframe_aware_for_overlapping_timestamps(
    tmp_path: Path,
) -> None:
    active = registry()
    one_hour_snapshot = feature_snapshot(0, timeframe="1h")
    one_minute_snapshot = feature_snapshot(0, timeframe="1m")
    one_hour = FeatureBuilder().update(one_hour_snapshot)
    one_minute = FeatureBuilder().update(one_minute_snapshot)
    manifest = publish_feature_rows(
        (one_hour_snapshot, one_minute_snapshot),
        output_root=tmp_path,
        identity=identity(active),
        registry=active,
        leakage_audit=leakage_approval(active),
        max_rows_per_part=1,
    )

    one_hour_record = feature_partition_records(manifest, (one_hour,))
    one_minute_record = feature_partition_records(manifest, (one_minute,))

    assert one_hour_record != one_minute_record
    assert "/timeframe=1h/" in one_hour_record[0].path
    assert "/timeframe=1m/" in one_minute_record[0].path


def test_empty_publication_is_explicit_and_idempotent(tmp_path: Path) -> None:
    active = registry()
    frozen = identity(active)
    first = publish_feature_rows(
        [],
        output_root=tmp_path,
        identity=frozen,
        registry=active,
        leakage_audit=leakage_approval(active),
    )
    second = publish_feature_rows(
        [],
        output_root=tmp_path,
        identity=frozen,
        registry=active,
        leakage_audit=leakage_approval(active),
    )

    assert first == second
    assert first.row_count == 0
    assert first.partitions == ()
    assert first.min_timestamp is None
    published = tmp_path / "dataset_version=DS-000009" / "feature_set=FS-000001" / "features"
    assert read_derived_manifest(published / "manifest.json") == first


def test_existing_publication_rejects_identity_conflict_and_tampering(tmp_path: Path) -> None:
    active = registry()
    frozen = identity(active)
    manifest = publish_feature_rows(
        [feature_snapshot(0)],
        output_root=tmp_path,
        identity=frozen,
        registry=active,
        leakage_audit=leakage_approval(active),
    )
    assert (
        publish_feature_rows(
            [feature_snapshot(0)],
            output_root=tmp_path,
            identity=frozen,
            registry=active,
            leakage_audit=leakage_approval(active),
        )
        == manifest
    )
    with pytest.raises(FileExistsError, match="content conflicts"):
        publish_feature_rows(
            [feature_snapshot(0, value=0.2)],
            output_root=tmp_path,
            identity=frozen,
            registry=active,
            leakage_audit=leakage_approval(active),
        )
    with pytest.raises(FileExistsError, match="different identity"):
        publish_feature_rows(
            [feature_snapshot(0)],
            output_root=tmp_path,
            identity=replace(frozen, code_commit="fedcba9876543210"),
            registry=active,
            leakage_audit=leakage_approval(active),
        )

    published = tmp_path / "dataset_version=DS-000009" / "feature_set=FS-000001" / "features"
    partition = published / manifest.partitions[0].path
    partition.write_bytes(partition.read_bytes() + b"tamper")
    with pytest.raises(ValueError, match="checksum"):
        publish_feature_rows(
            [],
            output_root=tmp_path,
            identity=frozen,
            registry=active,
            leakage_audit=leakage_approval(active),
        )


def test_interrupted_publication_releases_owner_and_removes_unique_staging(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    active = registry()
    frozen = identity(active)
    batch = build_feature_batch(
        [feature_snapshot(index) for index in range(3)],
        artifact_path=tmp_path / "batch" / "rows.jsonl",
        maximum_rows=3,
    )
    replay = FeatureBuildBatch.iter_rows

    def interrupted(self, metadata=None) -> Iterator[FeatureRow]:
        for index, row in enumerate(replay(self, metadata)):
            if index == 2:
                raise RuntimeError("interrupted")
            yield row

    monkeypatch.setattr(FeatureBuildBatch, "iter_rows", interrupted)

    with pytest.raises(RuntimeError, match="interrupted"):
        publish_feature_batch(
            batch,
            output_root=tmp_path,
            identity=frozen,
            registry=active,
            leakage_audit=leakage_approval(active),
            max_rows_per_part=2,
        )
    base = tmp_path / "dataset_version=DS-000009" / "feature_set=FS-000001"
    assert list(base.glob(".features.partial.*")) == []
    assert not (base / ".features.publish.lock").exists()
    monkeypatch.setattr(FeatureBuildBatch, "iter_rows", replay)
    manifest = publish_feature_rows(
        [feature_snapshot(0, value=0.5), feature_snapshot(1)],
        output_root=tmp_path,
        identity=frozen,
        registry=active,
        leakage_audit=leakage_approval(active),
        max_rows_per_part=2,
    )
    assert manifest.row_count == 2
    assert list(base.glob(".features.partial.*")) == []


@pytest.mark.parametrize("conflicting", (False, True))
def test_concurrent_publishers_have_exclusive_owner_and_unique_staging(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    conflicting: bool,
) -> None:
    active = registry()
    frozen = identity(active)
    first = build_feature_batch(
        [feature_snapshot(0)],
        artifact_path=tmp_path / "batch" / "first.jsonl",
        maximum_rows=1,
    )
    second = build_feature_batch(
        [feature_snapshot(0, value=0.2 if conflicting else 0.0)],
        artifact_path=tmp_path / "batch" / "second.jsonl",
        maximum_rows=1,
    )
    replay = FeatureBuildBatch.iter_rows
    winner_entered = Event()
    resume_winner = Event()
    replayed_batches: list[FeatureBuildBatch] = []

    def paused_replay(self, lease=None) -> Iterator[FeatureRow]:
        replayed_batches.append(self)
        if self is first:
            winner_entered.set()
            assert resume_winner.wait(timeout=10)
        yield from replay(self, lease)

    monkeypatch.setattr(FeatureBuildBatch, "iter_rows", paused_replay)
    manifests = []
    errors: list[BaseException] = []

    def publish(batch: FeatureBuildBatch) -> None:
        try:
            manifests.append(
                publish_feature_batch(
                    batch,
                    output_root=tmp_path,
                    identity=frozen,
                    registry=active,
                    leakage_audit=leakage_approval(active),
                )
            )
        except BaseException as error:
            errors.append(error)

    winner = Thread(target=publish, args=(first,))
    winner.start()
    assert winner_entered.wait(timeout=10)
    loser = Thread(target=publish, args=(second,))
    loser.start()
    loser.join(timeout=10)
    assert not loser.is_alive()
    resume_winner.set()
    winner.join(timeout=10)

    assert not winner.is_alive()
    assert len(manifests) == 1
    assert len(errors) == 1
    assert isinstance(errors[0], PublicationBusyError)
    assert replayed_batches == [first]
    published = tmp_path / "dataset_version=DS-000009" / "feature_set=FS-000001" / "features"
    verify_derived_publication(published, manifests[0])
    base = published.parent
    assert list(base.glob(".features.partial.*")) == []
    assert not (base / ".features.publish.lock").exists()


def test_preexisting_publication_lock_is_never_stolen_or_consumed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    active = registry()
    frozen = identity(active)
    batch = build_feature_batch(
        [feature_snapshot(0)],
        artifact_path=tmp_path / "batch" / "rows.jsonl",
        maximum_rows=1,
    )
    base = tmp_path / "dataset_version=DS-000009" / "feature_set=FS-000001"
    base.mkdir(parents=True)
    lock = base / ".features.publish.lock"
    lock.write_text('{"schema_version":1,"status":"owner-active"}\n', encoding="utf-8")
    replayed = 0

    def forbidden_replay(self, lease=None) -> Iterator[FeatureRow]:
        nonlocal replayed
        replayed += 1
        raise AssertionError("locked publication consumed producer rows")
        yield

    monkeypatch.setattr(FeatureBuildBatch, "iter_rows", forbidden_replay)
    with pytest.raises(PublicationBusyError, match="active or stale owner lock"):
        publish_feature_batch(
            batch,
            output_root=tmp_path,
            identity=frozen,
            registry=active,
            leakage_audit=leakage_approval(active),
        )

    assert replayed == 0
    assert lock.read_text(encoding="utf-8") == ('{"schema_version":1,"status":"owner-active"}\n')
    assert list(base.glob(".features.partial.*")) == []


def test_failed_staging_cleanup_retains_owner_lock_and_blocks_later_publishers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    active = registry()
    frozen = identity(active)
    first = build_feature_batch(
        [feature_snapshot(0)],
        artifact_path=tmp_path / "batch" / "first.jsonl",
        maximum_rows=1,
    )
    second = build_feature_batch(
        [feature_snapshot(0)],
        artifact_path=tmp_path / "batch" / "second.jsonl",
        maximum_rows=1,
    )
    original_rmtree = derived_data.shutil.rmtree
    replayed = 0

    def interrupted(self, lease=None) -> Iterator[FeatureRow]:
        nonlocal replayed
        replayed += 1
        raise RuntimeError("producer interrupted")
        yield

    def cleanup_fails(path: object) -> None:
        raise OSError(f"simulated cleanup failure: {Path(path).name}")

    monkeypatch.setattr(FeatureBuildBatch, "iter_rows", interrupted)
    monkeypatch.setattr(derived_data.shutil, "rmtree", cleanup_fails)
    with pytest.raises(PublicationCleanupError, match="recovery-required owner lock retained"):
        publish_feature_batch(
            first,
            output_root=tmp_path,
            identity=frozen,
            registry=active,
            leakage_audit=leakage_approval(active),
        )

    base = tmp_path / "dataset_version=DS-000009" / "feature_set=FS-000001"
    lock = base / ".features.publish.lock"
    staging = list(base.glob(".features.partial.*"))
    assert lock.is_file()
    assert len(staging) == 1 and staging[0].is_dir()
    lock_payload = lock.read_bytes()
    assert len(lock_payload) <= derived_data._MAX_PUBLICATION_LOCK_BYTES
    assert json.loads(lock_payload)["staging_name"] == staging[0].name

    monkeypatch.setattr(derived_data.shutil, "rmtree", original_rmtree)
    with pytest.raises(PublicationBusyError, match="active or stale owner lock"):
        publish_feature_batch(
            second,
            output_root=tmp_path,
            identity=frozen,
            registry=active,
            leakage_audit=leakage_approval(active),
        )
    assert replayed == 1
    assert lock.read_bytes() == lock_payload
    assert staging[0].is_dir()


def test_feature_producer_rejects_duplicate_and_out_of_order_snapshots(tmp_path: Path) -> None:
    active = registry()
    frozen = identity(active)
    with pytest.raises(ValueError, match="duplicate feature snapshot timestamp"):
        publish_feature_rows(
            [feature_snapshot(0), feature_snapshot(0)],
            output_root=tmp_path / "duplicate",
            identity=frozen,
            registry=active,
            leakage_audit=leakage_approval(active),
        )
    with pytest.raises(ValueError, match="out-of-order feature snapshot timestamp"):
        publish_feature_rows(
            [feature_snapshot(1), feature_snapshot(0)],
            output_root=tmp_path / "ordering",
            identity=frozen,
            registry=active,
            leakage_audit=leakage_approval(active),
        )


def test_event_publication_uses_fixed_schema_and_records_overlap(tmp_path: Path) -> None:
    active = registry()
    frozen = identity(active)
    row_one = feature_row(1)
    row_two = feature_row(2)
    events = [
        make_event(
            EventKind.ROLLING_WINDOW,
            row_one.timestamp - timedelta(minutes=1),
            row_one.information_cutoff,
            row_one,
            "rolling-v1",
            registry=active,
            exploratory=True,
            metadata={"width_bars": 2},
        ),
        make_event(
            EventKind.EXPANSION,
            row_two.timestamp - timedelta(minutes=1),
            row_two.information_cutoff,
            row_two,
            "expansion-v1",
            registry=active,
            metadata={"volume_ratio": 2.0},
        ),
    ]
    manifest = publish_market_events(
        events,
        output_root=tmp_path,
        identity=frozen,
        registry=active,
        max_rows_per_part=1,
    )
    published = tmp_path / "dataset_version=DS-000009" / "feature_set=FS-000001" / "events"
    frame = pl.read_parquet(published / manifest.partitions[0].path)

    assert manifest.evidence.event_count == 2
    assert manifest.evidence.overlap_pair_count == 1
    assert manifest.evidence.overlap_event_count == 2
    assert manifest.evidence.maximum_concurrency == 2
    assert manifest.evidence.overlap_event_ratio == 1.0
    assert manifest.evidence.event_trigger_versions == ("expansion-v1", "rolling-v1")
    assert "metadata_json" in frame.columns
    assert "metadata" not in frame.columns
    assert "feature_values" not in frame.columns
    verify_derived_publication(published)
    assert (
        publish_market_events(
            events,
            output_root=tmp_path,
            identity=frozen,
            registry=active,
            max_rows_per_part=1,
        )
        == manifest
    )


def test_duplicate_and_out_of_order_event_rows_fail_closed(tmp_path: Path) -> None:
    active = registry()
    frozen = identity(active)
    row_one = feature_row(1)
    row_two = feature_row(2)
    event_one = make_event(
        EventKind.FIXED_WINDOW,
        row_one.timestamp - timedelta(minutes=1),
        row_one.information_cutoff,
        row_one,
        "fixed-v1",
        registry=active,
    )
    event_two = make_event(
        EventKind.FIXED_WINDOW,
        row_two.timestamp - timedelta(minutes=1),
        row_two.information_cutoff,
        row_two,
        "fixed-v1",
        registry=active,
    )

    with pytest.raises(ValueError, match="duplicate events row"):
        publish_market_events(
            [event_one, event_one],
            output_root=tmp_path / "duplicate",
            identity=frozen,
            registry=active,
        )
    with pytest.raises(
        ValueError,
        match="events must be ordered by symbol, timeframe, segment, and start",
    ):
        publish_market_events(
            [event_two, event_one],
            output_root=tmp_path / "ordering",
            identity=frozen,
            registry=active,
        )
