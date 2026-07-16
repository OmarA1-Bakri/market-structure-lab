from __future__ import annotations

from collections.abc import Iterator
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import polars as pl
import pytest

from market_structure_lab.data.derived import (
    DerivedPublicationIdentity,
    publish_feature_rows,
    publish_market_events,
    read_derived_manifest,
    verify_derived_publication,
)
from market_structure_lab.events.models import EventKind, make_event
from market_structure_lab.features.models import FeatureRow
from market_structure_lab.features.registry import (
    FeatureDefinition,
    FeatureFamily,
    FeatureRegistry,
    FeatureValueKind,
    LeakageClass,
    MissingPolicy,
)


def registry() -> FeatureRegistry:
    return FeatureRegistry(
        "FS-000009",
        (
            FeatureDefinition(
                name="location",
                definition="Current observable location.",
                family=FeatureFamily.AUCTION,
                value_kind=FeatureValueKind.CATEGORY,
                units="category",
                required_prior_observations=0,
                missing_policy=MissingPolicy.ERROR,
                version="v1",
                leakage_class=LeakageClass.AT_CUTOFF,
                allowed_categories=("inside", "outside"),
            ),
            FeatureDefinition(
                name="log_return_1",
                definition="Current close divided by the prior close in log space.",
                family=FeatureFamily.SEQUENCE,
                value_kind=FeatureValueKind.FLOAT,
                units="log_return",
                required_prior_observations=1,
                missing_policy=MissingPolicy.NULL,
                version="v1",
                leakage_class=LeakageClass.TRAILING_ONLY,
            ),
        ),
    )


def identity(active_registry: FeatureRegistry | None = None) -> DerivedPublicationIdentity:
    active = active_registry or registry()
    return DerivedPublicationIdentity(
        dataset_version="DS-000009",
        dataset_snapshot_sha256="a" * 64,
        feature_set_id=active.feature_set_id,
        feature_registry_sha256=active.sha256,
        config_version="config-v1",
        profile_version="profile-v1",
        window_policy_id="rolling-20",
        event_version="event-v1",
        normalizer_artifact_sha256=None,
        code_commit="0123456789abcdef",
        uv_lock_sha256="b" * 64,
    )


def feature_row(
    minute: int,
    *,
    active_registry: FeatureRegistry | None = None,
    symbol: str = "BTCUSDT",
    value: float | None = 0.1,
    timeframe: str = "1m",
) -> FeatureRow:
    active = active_registry or registry()
    timestamp = datetime(2025, 1, 1, tzinfo=UTC) + timedelta(minutes=minute)
    return FeatureRow(
        timestamp=timestamp,
        information_cutoff=timestamp
        + (timedelta(hours=1) if timeframe == "1h" else timedelta(minutes=1)),
        symbol=symbol,
        timeframe=timeframe,
        segment_id=0,
        dataset_version="DS-000009",
        config_version="config-v1",
        profile_version="profile-v1",
        window_policy_id="rolling-20",
        feature_set_id=active.feature_set_id,
        registry_id=active.registry_id,
        values={"location": "inside", "log_return_1": value},
    )


def test_feature_publication_has_fixed_chunks_and_deterministic_paths(tmp_path: Path) -> None:
    active = registry()
    frozen = identity(active)
    rows = [feature_row(index, active_registry=active) for index in range(5)]

    first = publish_feature_rows(
        rows,
        output_root=tmp_path / "first",
        identity=frozen,
        registry=active,
        max_rows_per_part=2,
    )
    second = publish_feature_rows(
        iter(rows),
        output_root=tmp_path / "second",
        identity=frozen,
        registry=active,
        max_rows_per_part=2,
    )

    assert first == second
    assert [item.row_count for item in first.partitions] == [2, 2, 1]
    assert first.max_buffered_rows == 2
    assert first.evidence.null_value_count == 0
    assert first.partitions[0].path == ("symbol=BTCUSDT/year=2025/month=01/part-000001.parquet")
    published = (
        tmp_path / "first" / "dataset_version=DS-000009" / "feature_set=FS-000009" / "features"
    )
    assert (published / "_SUCCESS").is_file()
    verify_derived_publication(published)


def test_feature_columns_are_declared_columns_not_a_mapping_blob(tmp_path: Path) -> None:
    active = registry()
    manifest = publish_feature_rows(
        [feature_row(0, active_registry=active, value=None)],
        output_root=tmp_path,
        identity=identity(active),
        registry=active,
    )
    published = tmp_path / "dataset_version=DS-000009" / "feature_set=FS-000009" / "features"
    frame = pl.read_parquet(published / manifest.partitions[0].path)

    assert "values" not in frame.columns
    assert frame["location"].to_list() == ["inside"]
    assert frame["log_return_1"].to_list() == [None]
    assert manifest.evidence.null_value_count == 1
    assert manifest.evidence.warmup_row_count == 1


def test_partition_numbering_remains_unique_when_a_month_reappears(tmp_path: Path) -> None:
    active = registry()
    manifest = publish_feature_rows(
        [
            feature_row(0, active_registry=active, timeframe="1h"),
            feature_row(44_640, active_registry=active, timeframe="1h"),
            feature_row(0, active_registry=active, timeframe="1m"),
        ],
        output_root=tmp_path,
        identity=identity(active),
        registry=active,
        max_rows_per_part=1,
    )

    paths = tuple(item.path for item in manifest.partitions)
    assert paths == (
        "symbol=BTCUSDT/year=2025/month=01/part-000001.parquet",
        "symbol=BTCUSDT/year=2025/month=01/part-000002.parquet",
        "symbol=BTCUSDT/year=2025/month=02/part-000001.parquet",
    )


def test_empty_publication_is_explicit_and_idempotent(tmp_path: Path) -> None:
    active = registry()
    frozen = identity(active)
    first = publish_feature_rows([], output_root=tmp_path, identity=frozen, registry=active)
    second = publish_feature_rows([], output_root=tmp_path, identity=frozen, registry=active)

    assert first == second
    assert first.row_count == 0
    assert first.partitions == ()
    assert first.min_timestamp is None
    published = tmp_path / "dataset_version=DS-000009" / "feature_set=FS-000009" / "features"
    assert read_derived_manifest(published / "manifest.json") == first


def test_existing_publication_rejects_identity_conflict_and_tampering(tmp_path: Path) -> None:
    active = registry()
    frozen = identity(active)
    manifest = publish_feature_rows(
        [feature_row(0, active_registry=active)],
        output_root=tmp_path,
        identity=frozen,
        registry=active,
    )
    assert (
        publish_feature_rows(
            [feature_row(0, active_registry=active)],
            output_root=tmp_path,
            identity=frozen,
            registry=active,
        )
        == manifest
    )
    with pytest.raises(FileExistsError, match="content conflicts"):
        publish_feature_rows(
            [feature_row(0, active_registry=active, value=0.2)],
            output_root=tmp_path,
            identity=frozen,
            registry=active,
        )
    with pytest.raises(FileExistsError, match="different identity"):
        publish_feature_rows(
            [feature_row(0, active_registry=active)],
            output_root=tmp_path,
            identity=replace(frozen, code_commit="fedcba9876543210"),
            registry=active,
        )

    published = tmp_path / "dataset_version=DS-000009" / "feature_set=FS-000009" / "features"
    partition = published / manifest.partitions[0].path
    partition.write_bytes(partition.read_bytes() + b"tamper")
    with pytest.raises(ValueError, match="checksum"):
        publish_feature_rows([], output_root=tmp_path, identity=frozen, registry=active)


def test_interrupted_publication_resumes_and_rejects_conflicting_staging(
    tmp_path: Path,
) -> None:
    active = registry()
    frozen = identity(active)

    def interrupted() -> Iterator[FeatureRow]:
        yield feature_row(0, active_registry=active)
        yield feature_row(1, active_registry=active)
        raise RuntimeError("interrupted")

    with pytest.raises(RuntimeError, match="interrupted"):
        publish_feature_rows(
            interrupted(),
            output_root=tmp_path,
            identity=frozen,
            registry=active,
            max_rows_per_part=2,
        )
    staging = tmp_path / "dataset_version=DS-000009" / "feature_set=FS-000009" / ".features.partial"
    assert len(list(staging.rglob("*.parquet"))) == 1
    with pytest.raises(FileExistsError, match="stale staging partition conflicts"):
        publish_feature_rows(
            [
                feature_row(0, active_registry=active, value=0.5),
                feature_row(1, active_registry=active),
            ],
            output_root=tmp_path,
            identity=frozen,
            registry=active,
            max_rows_per_part=2,
        )
    with pytest.raises(FileExistsError, match="different pinned identity"):
        publish_feature_rows(
            [],
            output_root=tmp_path,
            identity=replace(frozen, code_commit="fedcba9876543210"),
            registry=active,
            max_rows_per_part=2,
        )

    manifest = publish_feature_rows(
        [feature_row(0, active_registry=active), feature_row(1, active_registry=active)],
        output_root=tmp_path,
        identity=frozen,
        registry=active,
        max_rows_per_part=2,
    )
    assert manifest.row_count == 2
    assert not staging.exists()


def test_duplicate_and_out_of_order_feature_rows_fail_closed(tmp_path: Path) -> None:
    active = registry()
    frozen = identity(active)
    with pytest.raises(ValueError, match="duplicate features row"):
        publish_feature_rows(
            [feature_row(0, active_registry=active), feature_row(0, active_registry=active)],
            output_root=tmp_path / "duplicate",
            identity=frozen,
            registry=active,
        )
    with pytest.raises(ValueError, match="out-of-order features row"):
        publish_feature_rows(
            [feature_row(1, active_registry=active), feature_row(0, active_registry=active)],
            output_root=tmp_path / "ordering",
            identity=frozen,
            registry=active,
        )


def test_event_publication_uses_fixed_schema_and_records_overlap(tmp_path: Path) -> None:
    active = registry()
    frozen = identity(active)
    row_one = feature_row(1, active_registry=active)
    row_two = feature_row(2, active_registry=active)
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
    published = tmp_path / "dataset_version=DS-000009" / "feature_set=FS-000009" / "events"
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
