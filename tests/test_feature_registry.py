from __future__ import annotations

import json
import math
from datetime import UTC, datetime, timedelta, timezone
from types import MappingProxyType

import pytest

from market_structure_lab.features.models import FeatureRow
from market_structure_lab.features.registry import (
    FeatureDefinition,
    FeatureFamily,
    FeatureRegistry,
    FeatureValueKind,
    LeakageClass,
    MissingPolicy,
)


def definition(
    name: str,
    *,
    family: FeatureFamily = FeatureFamily.AUCTION,
    value_kind: FeatureValueKind = FeatureValueKind.FLOAT,
    missing_policy: MissingPolicy = MissingPolicy.NULL,
    leakage_class: LeakageClass = LeakageClass.AT_CUTOFF,
    allowed_categories: tuple[str, ...] = (),
) -> FeatureDefinition:
    return FeatureDefinition(
        name=name,
        definition=f"Deterministic definition for {name}",
        family=family,
        value_kind=value_kind,
        units="fraction",
        required_prior_observations=1,
        missing_policy=missing_policy,
        version="v1",
        leakage_class=leakage_class,
        allowed_categories=allowed_categories,
    )


def registry(*definitions: FeatureDefinition) -> FeatureRegistry:
    return FeatureRegistry("FS-000123", definitions)


def row_for(
    item: FeatureRegistry,
    values: dict[str, float | int | str | None],
    *,
    timestamp: datetime = datetime(2025, 1, 1, tzinfo=UTC),
    cutoff: datetime | None = None,
    timeframe: str = "1m",
) -> FeatureRow:
    return FeatureRow(
        timestamp=timestamp,
        information_cutoff=cutoff or timestamp + timedelta(minutes=1),
        symbol="BTCUSDT",
        timeframe=timeframe,
        segment_id=1,
        dataset_version="dataset-v1",
        config_version="config-v1",
        profile_version="profile-v1",
        window_policy_id="rolling-bars-v1:60",
        feature_set_id=item.feature_set_id,
        registry_id=item.registry_id,
        values=values,
    )


def test_registry_canonical_payload_and_hash_ignore_insertion_order() -> None:
    auction = definition("poc_distance")
    sequence = definition(
        "return_1",
        family=FeatureFamily.SEQUENCE,
        leakage_class=LeakageClass.TRAILING_ONLY,
    )

    first = registry(auction, sequence)
    second = registry(sequence, auction)

    assert first.feature_set_id == "FS-000123"
    assert first.names == ("poc_distance", "return_1")
    assert first.definitions == second.definitions
    assert first.canonical_json() == second.canonical_json()
    assert first.sha256 == second.sha256
    assert first.registry_id == second.registry_id
    assert first.registry_id.startswith("FR-")
    assert json.loads(first.canonical_json())["feature_set_id"] == "FS-000123"


def test_registry_hash_is_golden_and_metadata_change_is_detectable() -> None:
    first = registry(definition("poc_distance"))
    changed = FeatureDefinition(
        **{
            **first.definitions[0].to_dict(),
            "family": FeatureFamily.AUCTION,
            "value_kind": FeatureValueKind.FLOAT,
            "missing_policy": MissingPolicy.NULL,
            "leakage_class": LeakageClass.AT_CUTOFF,
            "definition": "Changed, still deterministic definition",
            "allowed_categories": (),
        }
    )
    reused_id = registry(changed)

    assert first.sha256 == "704c8a61ed22ac30c56b911283ae22d084aae6ad5987003c49c08698823ae099"
    assert first.registry_id == "FR-704C8A61ED22"
    assert reused_id.feature_set_id == first.feature_set_id
    assert reused_id.sha256 != first.sha256
    assert reused_id.registry_id != first.registry_id


@pytest.mark.parametrize("feature_set_id", ["", "FS-1", "FS-ABCDEF", "fs-000001", "FS-0000000"])
def test_registry_requires_manually_allocated_decimal_feature_set_id(
    feature_set_id: str,
) -> None:
    with pytest.raises(ValueError, match="FS-######"):
        FeatureRegistry(feature_set_id, [definition("poc_distance")])


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"name": "Future Return"}, "name"),
        ({"definition": " "}, "definition"),
        ({"units": ""}, "units"),
        ({"version": "version with spaces"}, "version"),
        ({"required_prior_observations": -1}, "required_prior_observations"),
    ],
)
def test_feature_definition_rejects_unsafe_or_incomplete_metadata(
    changes: dict[str, object], message: str
) -> None:
    values: dict[str, object] = {
        "name": "poc_distance",
        "definition": "Distance from close to POC",
        "family": FeatureFamily.AUCTION,
        "value_kind": FeatureValueKind.FLOAT,
        "units": "fraction",
        "required_prior_observations": 0,
        "missing_policy": MissingPolicy.NULL,
        "version": "v1",
        "leakage_class": LeakageClass.AT_CUTOFF,
    }
    values.update(changes)

    with pytest.raises((TypeError, ValueError), match=message):
        FeatureDefinition(**values)  # type: ignore[arg-type]


def test_categories_are_declared_only_for_category_features() -> None:
    item = definition(
        "auction_location",
        value_kind=FeatureValueKind.CATEGORY,
        allowed_categories=("above_value", "inside_value", "below_value"),
    )
    assert item.allowed_categories == ("above_value", "below_value", "inside_value")

    with pytest.raises(ValueError, match="allowed_categories"):
        definition("unbounded_category", value_kind=FeatureValueKind.CATEGORY)
    with pytest.raises(ValueError, match="allowed_categories"):
        definition("numeric_feature", allowed_categories=("unexpected",))


def test_registry_rejects_duplicate_names_and_empty_definitions() -> None:
    with pytest.raises(ValueError, match="at least one"):
        FeatureRegistry("FS-000123", [])
    with pytest.raises(ValueError, match="duplicate feature name"):
        registry(definition("poc_distance"), definition("poc_distance"))


@pytest.mark.parametrize(
    "name",
    [
        "future_return_5",
        "forward_volatility",
        "mfe_30",
        "mae_30",
        "target_hit",
        "trade_profit",
        "outcome_label",
        "pnl_after_costs",
    ],
)
def test_discovery_registry_rejects_prohibited_future_or_outcome_names(name: str) -> None:
    with pytest.raises(ValueError, match="prohibited discovery feature name"):
        registry(definition(name))


def test_discovery_registry_rejects_outcome_or_future_leakage_class() -> None:
    with pytest.raises(ValueError, match="OUTCOME_OR_FUTURE"):
        registry(
            definition(
                "innocent_name",
                leakage_class=LeakageClass.OUTCOME_OR_FUTURE,
            )
        )


def test_feature_row_is_utc_cutoff_bound_and_immutable() -> None:
    item = registry(definition("return_1", family=FeatureFamily.SEQUENCE))
    values = {"return_1": 0.25}
    row = row_for(item, values)
    values["return_1"] = 999.0

    assert row.timestamp.tzinfo is UTC
    assert row.information_cutoff == datetime(2025, 1, 1, 0, 1, tzinfo=UTC)
    assert row.values == {"return_1": 0.25}
    assert isinstance(row.values, MappingProxyType)
    with pytest.raises(TypeError):
        row.values["return_1"] = 1.0  # type: ignore[index]


@pytest.mark.parametrize("timeframe, seconds", [("1m", 60), ("15m", 900), ("4h", 14_400), ("1d", 86_400)])
def test_feature_row_supports_explicit_timeframe_durations(timeframe: str, seconds: int) -> None:
    item = registry(definition("poc_distance"))
    timestamp = datetime(2025, 1, 1, tzinfo=UTC)
    candidate = row_for(
        item,
        {"poc_distance": 0.0},
        timestamp=timestamp,
        cutoff=timestamp + timedelta(seconds=seconds),
        timeframe=timeframe,
    )
    item.validate_row(candidate)


def test_feature_row_rejects_naive_non_utc_or_wrong_cutoff_and_invalid_timeframe() -> None:
    item = registry(definition("poc_distance"))
    with pytest.raises(ValueError, match="UTC-aware"):
        row_for(item, {"poc_distance": 0.0}, timestamp=datetime(2025, 1, 1))
    offset = timezone(timedelta(hours=1))
    with pytest.raises(ValueError, match="UTC"):
        row_for(item, {"poc_distance": 0.0}, timestamp=datetime(2025, 1, 1, tzinfo=offset))
    with pytest.raises(ValueError, match="information_cutoff"):
        row_for(item, {"poc_distance": 0.0}, cutoff=datetime(2025, 1, 1, 0, 2, tzinfo=UTC))
    with pytest.raises(ValueError, match="timeframe"):
        row_for(item, {"poc_distance": 0.0}, timeframe="monthly")


@pytest.mark.parametrize("segment_id", [-1, True, "segment-1"])
def test_feature_row_requires_non_negative_integer_segment_id(segment_id: object) -> None:
    item = registry(definition("poc_distance"))
    base = row_for(item, {"poc_distance": 0.0})
    with pytest.raises((TypeError, ValueError), match="segment_id"):
        FeatureRow(
            **{
                **base.metadata_dict(),
                "segment_id": segment_id,
                "values": {"poc_distance": 0.0},
            }
        )


def test_registry_validates_exact_ids_and_feature_columns() -> None:
    item = registry(definition("poc_distance"))
    item.validate_row(row_for(item, {"poc_distance": 0.0}))

    with pytest.raises(ValueError, match="feature_set_id"):
        item.validate_row(
            FeatureRow(
                **{
                    **row_for(item, {"poc_distance": 0.0}).metadata_dict(),
                    "feature_set_id": "FS-999999",
                    "values": {"poc_distance": 0.0},
                }
            )
        )
    with pytest.raises(ValueError, match="registry_id"):
        item.validate_row(
            FeatureRow(
                **{
                    **row_for(item, {"poc_distance": 0.0}).metadata_dict(),
                    "registry_id": "FR-000000000000",
                    "values": {"poc_distance": 0.0},
                }
            )
        )
    with pytest.raises(ValueError, match="feature columns"):
        item.validate_row(row_for(item, {"extra_feature": 0.0}))


@pytest.mark.parametrize("value", [math.nan, math.inf, -math.inf, True, "0.1"])
def test_float_features_reject_non_finite_or_wrong_types(value: object) -> None:
    item = registry(definition("poc_distance"))
    with pytest.raises((TypeError, ValueError), match="poc_distance"):
        item.validate_row(row_for(item, {"poc_distance": value}))  # type: ignore[dict-item]


def test_null_integer_and_category_values_follow_their_declarations() -> None:
    item = registry(
        definition("poc_distance"),
        definition("run_length", value_kind=FeatureValueKind.INTEGER),
        definition(
            "auction_location",
            value_kind=FeatureValueKind.CATEGORY,
            allowed_categories=("inside", "above", "below"),
        ),
    )
    item.validate_row(
        row_for(
            item,
            {"poc_distance": None, "run_length": 2, "auction_location": "inside"},
        )
    )
    with pytest.raises(TypeError, match="run_length"):
        item.validate_row(
            row_for(
                item,
                {"poc_distance": 0.0, "run_length": 2.0, "auction_location": "inside"},
            )
        )
    with pytest.raises(ValueError, match="auction_location"):
        item.validate_row(
            row_for(
                item,
                {"poc_distance": 0.0, "run_length": 2, "auction_location": "unknown"},
            )
        )


def test_error_missing_policy_rejects_null() -> None:
    item = registry(definition("poc_distance", missing_policy=MissingPolicy.ERROR))
    with pytest.raises(ValueError, match="poc_distance"):
        item.validate_row(row_for(item, {"poc_distance": None}))


def test_feature_row_canonical_json_orders_values_and_metadata() -> None:
    item = registry(
        definition("poc_distance"),
        definition("return_1", family=FeatureFamily.SEQUENCE),
    )
    first = row_for(item, {"return_1": -0.1, "poc_distance": 0.2})
    second = row_for(item, {"poc_distance": 0.2, "return_1": -0.1})

    assert first.canonical_json() == second.canonical_json()
    payload = json.loads(first.canonical_json())
    assert list(payload["values"]) == ["poc_distance", "return_1"]
    assert payload["timestamp"] == "2025-01-01T00:00:00Z"
    assert payload["information_cutoff"] == "2025-01-01T00:01:00Z"
