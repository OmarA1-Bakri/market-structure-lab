from __future__ import annotations

import json
import math
from dataclasses import replace
from datetime import UTC, datetime, timedelta, timezone
from types import MappingProxyType

import pytest

from market_structure_lab.features.models import FeatureRow
from market_structure_lab.features.registry import (
    BUILTIN_FEATURE_BUILDER_ID,
    BUILTIN_FEATURE_BUILDER_VERSION,
    MAX_FEATURE_CATEGORIES,
    MAX_FEATURE_DEFINITION_LENGTH,
    MAX_FEATURE_SOURCE_FIELDS,
    FeatureDefinition,
    FeatureFamily,
    FeatureRegistry,
    FeatureValueKind,
    LeakageClass,
    MissingPolicy,
    NormalizationRequirement,
    ObservableCutoffRule,
)


def definition(
    name: str,
    *,
    family: FeatureFamily = FeatureFamily.AUCTION,
    value_kind: FeatureValueKind = FeatureValueKind.FLOAT,
    missing_policy: MissingPolicy = MissingPolicy.NULL,
    leakage_class: LeakageClass = LeakageClass.AT_CUTOFF,
    allowed_categories: tuple[str, ...] = (),
    source_fields: tuple[str, ...] = ("snapshot.latest_candle.close",),
    trailing_window: str = "trailing_2_observations",
    observable_cutoff_rule: ObservableCutoffRule = (
        ObservableCutoffRule.TRAILING_THROUGH_INFORMATION_CUTOFF
    ),
    normalization_requirement: NormalizationRequirement = (
        NormalizationRequirement.TRAINING_PARTITION_FITTED
    ),
    future_outcome_prohibited: bool = True,
    builder_id: str = BUILTIN_FEATURE_BUILDER_ID,
    builder_version: str = BUILTIN_FEATURE_BUILDER_VERSION,
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
        source_fields=source_fields,
        trailing_window=trailing_window,
        observable_cutoff_rule=observable_cutoff_rule,
        normalization_requirement=normalization_requirement,
        future_outcome_prohibited=future_outcome_prohibited,
        builder_id=builder_id,
        builder_version=builder_version,
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


def test_registry_snapshot_is_detached_frozen_and_authoritative() -> None:
    active = registry(definition("poc_distance"), definition("return_1"))
    snapshot = active.snapshot()
    expected_json = snapshot.canonical_json
    expected_names = snapshot.names
    expected_dependency = snapshot.dependency_contract_sha256

    object.__setattr__(active._definitions[0], "source_fields", ("snapshot.future.close",))
    active._names = tuple(reversed(active._names))

    assert snapshot.canonical_json == expected_json
    assert snapshot.names == expected_names
    assert snapshot.dependency_contract_sha256 == expected_dependency
    snapshot.audit_discovery()
    with pytest.raises(ValueError, match="future or outcome source"):
        active.snapshot()


def test_registry_hash_is_golden_and_metadata_change_is_detectable() -> None:
    first = registry(definition("poc_distance"))
    changed = replace(
        first.definitions[0],
        definition="Changed, still deterministic definition",
    )
    reused_id = registry(changed)

    assert first.sha256 == "2834616ec63d0a8e5f0dcea96fd3cfc1d39a7adc1c64011ad402b841186dda80"
    assert first.registry_id == "FR-2834616EC63D"
    assert reused_id.feature_set_id == first.feature_set_id
    assert reused_id.sha256 != first.sha256
    assert reused_id.registry_id != first.registry_id


def test_feature_dependency_contract_is_complete_and_hashed() -> None:
    item = definition("price_shape")
    payload = item.to_dict()

    assert payload["source_fields"] == ["snapshot.latest_candle.close"]
    assert payload["trailing_window"] == "trailing_2_observations"
    assert payload["observable_cutoff_rule"] == ("trailing_through_information_cutoff")
    assert payload["required_prior_observations"] == 1
    assert payload["normalization_requirement"] == "training_partition_fitted"
    assert payload["future_outcome_prohibited"] is True
    assert payload["builder_id"] == BUILTIN_FEATURE_BUILDER_ID
    assert payload["builder_version"] == BUILTIN_FEATURE_BUILDER_VERSION
    assert len(item.dependency_contract_sha256) == 64

    base = registry(item)
    for changes in (
        {"source_fields": ("snapshot.latest_candle.volume",)},
        {
            "trailing_window": "trailing_3_observations",
            "required_prior_observations": 2,
        },
        {
            "observable_cutoff_rule": ObservableCutoffRule.AT_INFORMATION_CUTOFF,
            "trailing_window": "current_observation",
            "required_prior_observations": 0,
        },
        {"normalization_requirement": NormalizationRequirement.NOT_REQUIRED},
    ):
        changed = registry(replace(item, **changes))
        assert changed.sha256 != base.sha256
    assert (
        replace(
            item,
            builder_version="causal-feature-builder-v3",
        ).dependency_contract_sha256
        != item.dependency_contract_sha256
    )


def test_feature_definition_accepts_exact_caps_and_rejects_one_over() -> None:
    exact_sources = tuple(
        f"snapshot.field_{index:02d}" for index in range(MAX_FEATURE_SOURCE_FIELDS)
    )
    exact_categories = tuple(f"category_{index:03d}" for index in range(MAX_FEATURE_CATEGORIES))
    exact = replace(
        definition("bounded_feature"),
        definition="d" * MAX_FEATURE_DEFINITION_LENGTH,
        source_fields=exact_sources,
    )
    category = replace(
        definition(
            "bounded_category",
            value_kind=FeatureValueKind.CATEGORY,
            allowed_categories=exact_categories,
            normalization_requirement=NormalizationRequirement.NOT_REQUIRED,
        ),
        allowed_categories=exact_categories,
    )

    assert len(exact.source_fields) == MAX_FEATURE_SOURCE_FIELDS
    assert len(category.allowed_categories) == MAX_FEATURE_CATEGORIES
    with pytest.raises(ValueError, match="source_fields"):
        replace(exact, source_fields=(*exact_sources, "snapshot.too_many"))
    with pytest.raises(ValueError, match="definition"):
        replace(exact, definition="d" * (MAX_FEATURE_DEFINITION_LENGTH + 1))
    with pytest.raises(ValueError, match="allowed_categories"):
        replace(category, allowed_categories=(*exact_categories, "too_many"))
    with pytest.raises(ValueError, match="name"):
        replace(exact, name="a" * 128)
    with pytest.raises(ValueError, match="version"):
        replace(exact, version="v" * 128)
    with pytest.raises(ValueError, match="trailing_window"):
        replace(exact, trailing_window="trailing_" + "9" * 65 + "_observations")


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
        ({"future_outcome_prohibited": False}, "future/outcome prohibition"),
        ({"builder_version": "unregistered-v1"}, "registered builder"),
    ],
)
def test_innocent_feature_names_cannot_hide_semantic_leakage(
    changes: dict[str, object],
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        registry(replace(definition("price_shape"), **changes))


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
        "source_fields": ("snapshot.latest_candle.close",),
        "trailing_window": "current_observation",
        "observable_cutoff_rule": ObservableCutoffRule.AT_INFORMATION_CUTOFF,
        "normalization_requirement": NormalizationRequirement.TRAINING_PARTITION_FITTED,
        "future_outcome_prohibited": True,
        "builder_id": BUILTIN_FEATURE_BUILDER_ID,
        "builder_version": BUILTIN_FEATURE_BUILDER_VERSION,
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
    with pytest.raises(ValueError, match="prohibited outcome or future discovery feature name"):
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


@pytest.mark.parametrize(
    "timeframe, seconds", [("1m", 60), ("15m", 900), ("4h", 14_400), ("1d", 86_400)]
)
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
