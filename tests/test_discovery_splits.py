from __future__ import annotations

from collections.abc import Iterator
from dataclasses import FrozenInstanceError, replace
from datetime import UTC, datetime, timedelta, timezone

import pytest

from market_structure_lab.discovery.splits import (
    DiscoveryInput,
    FrozenDiscoverySplit,
    PartitionRole,
    TimePartition,
    freeze_split,
    make_discovery_input,
)
from market_structure_lab.features.models import FeatureRow
from market_structure_lab.features.registry import (
    FeatureDefinition,
    FeatureFamily,
    FeatureRegistry,
    FeatureValueKind,
    LeakageClass,
    MissingPolicy,
)


def _registry() -> FeatureRegistry:
    return FeatureRegistry(
        "FS-000401",
        [
            FeatureDefinition(
                name="signal",
                definition="A deterministic cutoff-bound signal",
                family=FeatureFamily.SEQUENCE,
                value_kind=FeatureValueKind.FLOAT,
                units="fraction",
                required_prior_observations=1,
                missing_policy=MissingPolicy.ERROR,
                version="v1",
                leakage_class=LeakageClass.AT_CUTOFF,
            )
        ],
    )


def _partition(
    role: PartitionRole,
    start: datetime,
    end: datetime,
    *,
    symbols: tuple[str, ...] = ("BTCUSDT", "ETHUSDT"),
) -> TimePartition:
    return TimePartition(role=role, start=start, end=end, symbols=symbols)


def _partitions() -> tuple[TimePartition, TimePartition, TimePartition]:
    return (
        _partition(
            PartitionRole.DISCOVERY,
            datetime(2025, 1, 1, tzinfo=UTC),
            datetime(2025, 2, 1, tzinfo=UTC),
        ),
        _partition(
            PartitionRole.DEVELOPMENT,
            datetime(2025, 2, 1, tzinfo=UTC),
            datetime(2025, 3, 1, tzinfo=UTC),
        ),
        _partition(
            PartitionRole.HOLDOUT,
            datetime(2025, 3, 1, tzinfo=UTC),
            datetime(2025, 4, 1, tzinfo=UTC),
        ),
    )


def _row(
    registry: FeatureRegistry,
    *,
    timestamp: datetime,
    symbol: str = "BTCUSDT",
    dataset_version: str = "DS-000401",
    value: float = 1.0,
) -> FeatureRow:
    return FeatureRow(
        timestamp=timestamp,
        information_cutoff=timestamp + timedelta(minutes=1),
        symbol=symbol,
        timeframe="1m",
        segment_id=1,
        dataset_version=dataset_version,
        config_version="config-v1",
        profile_version="profile-v1",
        window_policy_id="rolling-bars-v1:60",
        feature_set_id=registry.feature_set_id,
        registry_id=registry.registry_id,
        values={"signal": value},
    )


def test_time_partition_is_immutable_utc_half_open_and_canonicalizes_symbols() -> None:
    start = datetime(2025, 1, 1, tzinfo=UTC)
    end = datetime(2025, 2, 1, tzinfo=UTC)
    partition = _partition(
        PartitionRole.DISCOVERY,
        start,
        end,
        symbols=("ETHUSDT", "BTCUSDT"),
    )

    assert partition.symbols == ("BTCUSDT", "ETHUSDT")
    assert partition.contains(start)
    assert partition.contains(end - timedelta(microseconds=1))
    assert not partition.contains(end)
    with pytest.raises(FrozenInstanceError):
        partition.end = end + timedelta(days=1)  # type: ignore[misc]


@pytest.mark.parametrize(
    ("start", "end", "message"),
    [
        (datetime(2025, 1, 1), datetime(2025, 2, 1, tzinfo=UTC), "UTC-aware"),
        (
            datetime(2025, 1, 1, tzinfo=timezone(timedelta(hours=1))),
            datetime(2025, 2, 1, tzinfo=UTC),
            "UTC",
        ),
        (
            datetime(2025, 2, 1, tzinfo=UTC),
            datetime(2025, 2, 1, tzinfo=UTC),
            "precede",
        ),
    ],
)
def test_time_partition_rejects_non_utc_or_empty_intervals(
    start: datetime, end: datetime, message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        _partition(PartitionRole.DISCOVERY, start, end)


def test_time_partition_requires_unique_non_empty_symbols() -> None:
    start = datetime(2025, 1, 1, tzinfo=UTC)
    end = datetime(2025, 2, 1, tzinfo=UTC)
    with pytest.raises(ValueError, match="symbols"):
        _partition(PartitionRole.DISCOVERY, start, end, symbols=())
    with pytest.raises(ValueError, match="symbols"):
        _partition(PartitionRole.DISCOVERY, start, end, symbols=("BTCUSDT", "BTCUSDT"))
    with pytest.raises(ValueError, match="symbols"):
        _partition(PartitionRole.DISCOVERY, start, end, symbols=("",))


def test_freeze_split_is_canonical_and_hashes_all_policy_fields() -> None:
    discovery, development, holdout = _partitions()
    first = freeze_split(
        split_id="split-v1",
        discovery=replace(discovery, symbols=("ETHUSDT", "BTCUSDT")),
        development=replace(development, symbols=("ETHUSDT", "BTCUSDT")),
        holdout=replace(holdout, symbols=("ETHUSDT", "BTCUSDT")),
        asset_holdouts=("SOLUSDT",),
    )
    second = freeze_split(
        split_id="split-v1",
        discovery=discovery,
        development=development,
        holdout=holdout,
        asset_holdouts=("SOLUSDT",),
    )

    assert first == second
    assert first.asset_holdouts == ("SOLUSDT",)
    assert first.sha256 == "379986f121dfd2f8739b445cccc78eb210222f41673b9de3ae6dc5033ef4369b"
    with pytest.raises(FrozenInstanceError):
        first.split_id = "split-v2"  # type: ignore[misc]
    changed = freeze_split(
        split_id="split-v1",
        discovery=discovery,
        development=development,
        holdout=holdout,
        asset_holdouts=("XRPUSDT",),
    )
    assert changed.sha256 != first.sha256


def test_frozen_split_rejects_a_forged_digest_from_canonical_content() -> None:
    discovery, development, holdout = _partitions()
    valid = freeze_split(
        split_id="split-v1",
        discovery=discovery,
        development=development,
        holdout=holdout,
        asset_holdouts=("SOLUSDT",),
    )

    with pytest.raises(ValueError, match="split.*SHA-256|digest"):
        FrozenDiscoverySplit(
            split_id=valid.split_id,
            discovery=valid.discovery,
            development=valid.development,
            holdout=valid.holdout,
            asset_holdouts=valid.asset_holdouts,
            sha256="0" * 64,
        )


def test_freeze_split_requires_roles_shared_universe_and_chronological_order() -> None:
    discovery, development, holdout = _partitions()
    with pytest.raises(ValueError, match="discovery partition role"):
        freeze_split(
            split_id="split-v1",
            discovery=replace(discovery, role=PartitionRole.DEVELOPMENT),
            development=development,
            holdout=holdout,
        )
    with pytest.raises(ValueError, match="same symbols"):
        freeze_split(
            split_id="split-v1",
            discovery=discovery,
            development=replace(development, symbols=("BTCUSDT",)),
            holdout=holdout,
        )
    with pytest.raises(ValueError, match="chronological"):
        freeze_split(
            split_id="split-v1",
            discovery=replace(discovery, end=development.start + timedelta(days=1)),
            development=development,
            holdout=holdout,
        )


def test_freeze_split_rejects_asset_holdout_leakage_and_invalid_names() -> None:
    discovery, development, holdout = _partitions()
    with pytest.raises(ValueError, match="asset holdouts"):
        freeze_split(
            split_id="split-v1",
            discovery=discovery,
            development=development,
            holdout=holdout,
            asset_holdouts=("BTCUSDT",),
        )
    with pytest.raises(ValueError, match="asset_holdouts"):
        freeze_split(
            split_id="split-v1",
            discovery=discovery,
            development=development,
            holdout=holdout,
            asset_holdouts=("SOLUSDT", "SOLUSDT"),
        )


class _ExplodingRows:
    def __iter__(self) -> Iterator[FeatureRow]:
        raise AssertionError("the holdout iterable was touched")


@pytest.mark.parametrize("purpose", ["fit", "stability"])
def test_make_discovery_input_rejects_holdout_before_touching_rows(purpose: str) -> None:
    _, _, holdout = _partitions()

    with pytest.raises(ValueError, match="holdout"):
        make_discovery_input(
            partition=holdout,
            rows=_ExplodingRows(),
            registry=_registry(),
            purpose=purpose,  # type: ignore[arg-type]
            max_rows=1,
        )


def test_fit_accepts_only_discovery_and_rejects_development_before_iteration() -> None:
    _, development, _ = _partitions()

    with pytest.raises(ValueError, match="fit.*discovery"):
        make_discovery_input(
            partition=development,
            rows=_ExplodingRows(),
            registry=_registry(),
            purpose="fit",
            max_rows=1,
        )


def test_stability_accepts_development() -> None:
    _, partition, _ = _partitions()
    registry = _registry()
    timestamp = partition.start
    candidate = _row(registry, timestamp=timestamp)

    result = make_discovery_input(
        partition=partition,
        rows=[candidate],
        registry=registry,
        purpose="stability",
        max_rows=1,
    )

    assert isinstance(result, DiscoveryInput)
    assert result.partition is partition
    assert result.rows == (candidate,)
    assert result.dataset_version == "DS-000401"
    assert result.feature_set_id == registry.feature_set_id
    assert result.registry_id == registry.registry_id


def test_stability_rejects_discovery_before_iteration() -> None:
    discovery, _, _ = _partitions()

    with pytest.raises(ValueError, match="stability.*development"):
        make_discovery_input(
            partition=discovery,
            rows=_ExplodingRows(),
            registry=_registry(),
            purpose="stability",
            max_rows=1,
        )


def test_direct_discovery_input_construction_is_rejected() -> None:
    discovery, _, _ = _partitions()
    registry = _registry()
    candidate = _row(registry, timestamp=discovery.start)

    with pytest.raises(TypeError, match="make_discovery_input"):
        DiscoveryInput(
            partition=discovery,
            rows=(candidate,),
            dataset_version=candidate.dataset_version,
            feature_set_id=registry.feature_set_id,
            registry_id=registry.registry_id,
        )


def test_direct_discovery_input_cannot_bypass_holdout_guard() -> None:
    _, _, holdout = _partitions()
    registry = _registry()
    candidate = _row(registry, timestamp=holdout.start)

    with pytest.raises(TypeError, match="make_discovery_input"):
        DiscoveryInput(
            partition=holdout,
            rows=(candidate,),
            dataset_version=candidate.dataset_version,
            feature_set_id=registry.feature_set_id,
            registry_id=registry.registry_id,
        )


def test_make_discovery_input_preserves_valid_order_and_exact_row_identities() -> None:
    discovery, _, _ = _partitions()
    registry = _registry()
    first = _row(registry, timestamp=discovery.start, symbol="BTCUSDT")
    second = _row(
        registry,
        timestamp=discovery.start + timedelta(minutes=1),
        symbol="BTCUSDT",
        value=2.0,
    )

    result = make_discovery_input(
        partition=discovery,
        rows=iter((first, second)),
        registry=registry,
        purpose="fit",
        max_rows=2,
    )

    assert result.rows[0] is first
    assert result.rows[1] is second


def test_make_discovery_input_rejects_identity_drift_and_unsorted_rows() -> None:
    discovery, _, _ = _partitions()
    registry = _registry()
    first = _row(registry, timestamp=discovery.start)
    second = _row(registry, timestamp=discovery.start + timedelta(minutes=1))

    with pytest.raises(ValueError, match="dataset version"):
        make_discovery_input(
            partition=discovery,
            rows=(first, replace(second, dataset_version="DS-999999")),
            registry=registry,
            purpose="fit",
            max_rows=2,
        )
    for field in ("config_version", "profile_version", "window_policy_id"):
        with pytest.raises(ValueError, match=field):
            make_discovery_input(
                partition=discovery,
                rows=(first, replace(second, **{field: "drifted-v2"})),
                registry=registry,
                purpose="fit",
                max_rows=2,
            )
    with pytest.raises(ValueError, match="ordered"):
        make_discovery_input(
            partition=discovery,
            rows=(second, first),
            registry=registry,
            purpose="fit",
            max_rows=2,
        )


def test_make_discovery_input_rejects_duplicate_feature_identity() -> None:
    discovery, _, _ = _partitions()
    registry = _registry()
    candidate = _row(registry, timestamp=discovery.start)

    with pytest.raises(ValueError, match="duplicate"):
        make_discovery_input(
            partition=discovery,
            rows=(candidate, candidate),
            registry=registry,
            purpose="fit",
            max_rows=2,
        )


def test_make_discovery_input_rejects_out_of_partition_symbol_cutoff_and_row_limit() -> None:
    discovery, _, _ = _partitions()
    registry = _registry()
    valid = _row(registry, timestamp=discovery.start)

    with pytest.raises(ValueError, match="symbol"):
        make_discovery_input(
            partition=discovery,
            rows=(replace(valid, symbol="SOLUSDT"),),
            registry=registry,
            purpose="fit",
            max_rows=1,
        )
    with pytest.raises(ValueError, match="partition"):
        make_discovery_input(
            partition=discovery,
            rows=(_row(registry, timestamp=discovery.end - timedelta(minutes=1)),),
            registry=registry,
            purpose="fit",
            max_rows=1,
        )
    with pytest.raises(ValueError, match="max_rows"):
        make_discovery_input(
            partition=discovery,
            rows=(
                valid,
                replace(
                    valid,
                    timestamp=valid.timestamp + timedelta(minutes=1),
                    information_cutoff=valid.information_cutoff + timedelta(minutes=1),
                ),
            ),
            registry=registry,
            purpose="fit",
            max_rows=1,
        )


@pytest.mark.parametrize("max_rows", [0, -1, True])
def test_make_discovery_input_rejects_invalid_limits_before_iteration(max_rows: object) -> None:
    discovery, _, _ = _partitions()

    with pytest.raises(ValueError, match="max_rows"):
        make_discovery_input(
            partition=discovery,
            rows=_ExplodingRows(),
            registry=_registry(),
            purpose="fit",
            max_rows=max_rows,  # type: ignore[arg-type]
        )


def test_make_discovery_input_rejects_empty_rows_and_unknown_purpose() -> None:
    discovery, _, _ = _partitions()
    registry = _registry()
    with pytest.raises(ValueError, match="at least one"):
        make_discovery_input(
            partition=discovery,
            rows=(),
            registry=registry,
            purpose="fit",
            max_rows=1,
        )
    with pytest.raises(ValueError, match="purpose"):
        make_discovery_input(
            partition=discovery,
            rows=_ExplodingRows(),
            registry=registry,
            purpose="describe",  # type: ignore[arg-type]
            max_rows=1,
        )
