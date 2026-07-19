from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from market_structure_lab.discovery import (
    PartitionRole,
    TimePartition,
    build_feature_matrix,
    make_discovery_input,
)
from market_structure_lab.features.models import FeatureRow
from market_structure_lab.features.registry import (
    BUILTIN_FEATURE_BUILDER_ID,
    BUILTIN_FEATURE_BUILDER_VERSION,
    FeatureDefinition,
    FeatureFamily,
    FeatureRegistry,
    FeatureValueKind,
    LeakageClass,
    MissingPolicy,
    NormalizationRequirement,
    ObservableCutoffRule,
)


def _definition(name: str, kind: FeatureValueKind) -> FeatureDefinition:
    return FeatureDefinition(
        name=name,
        definition=f"Test definition for {name}",
        family=FeatureFamily.SEQUENCE,
        value_kind=kind,
        units="ratio",
        required_prior_observations=0,
        missing_policy=MissingPolicy.NULL,
        version="1.0.0",
        leakage_class=LeakageClass.AT_CUTOFF,
        source_fields=(f"fixture.{name}",),
        trailing_window="current_observation",
        observable_cutoff_rule=ObservableCutoffRule.AT_INFORMATION_CUTOFF,
        normalization_requirement=(
            NormalizationRequirement.NOT_REQUIRED
            if kind is FeatureValueKind.CATEGORY
            else NormalizationRequirement.TRAINING_PARTITION_FITTED
        ),
        future_outcome_prohibited=True,
        builder_id=BUILTIN_FEATURE_BUILDER_ID,
        builder_version=BUILTIN_FEATURE_BUILDER_VERSION,
        allowed_categories=("a", "b") if kind is FeatureValueKind.CATEGORY else (),
    )


def _registry(feature_set_id: str = "FS-000401") -> FeatureRegistry:
    return FeatureRegistry(
        feature_set_id,
        [
            _definition("category_state", FeatureValueKind.CATEGORY),
            _definition("integer_count", FeatureValueKind.INTEGER),
            _definition("numeric_value", FeatureValueKind.FLOAT),
        ],
    )


def _input(
    registry: FeatureRegistry,
    values: list[dict[str, float | int | str | None]],
):
    start = datetime(2025, 1, 1, tzinfo=UTC)
    partition = TimePartition(
        role=PartitionRole.DISCOVERY,
        start=start,
        end=start + timedelta(hours=1),
        symbols=("BTCUSDT",),
    )
    rows = [
        FeatureRow(
            timestamp=start + timedelta(minutes=index),
            information_cutoff=start + timedelta(minutes=index + 1),
            symbol="BTCUSDT",
            timeframe="1m",
            segment_id=0,
            dataset_version="DS-TEST",
            config_version="cfg-1",
            profile_version="profile-1",
            window_policy_id="window-1",
            feature_set_id=registry.feature_set_id,
            registry_id=registry.registry_id,
            values=row_values,
        )
        for index, row_values in enumerate(values)
    ]
    return make_discovery_input(
        partition=partition,
        rows=rows,
        registry=registry,
        purpose="fit",
        max_rows=len(rows),
    )


def test_matrix_selects_registered_numeric_features_in_registry_order() -> None:
    registry = _registry()
    discovery_input = _input(
        registry,
        [
            {"category_state": "a", "integer_count": 2, "numeric_value": 1.5},
            {"category_state": "b", "integer_count": 4, "numeric_value": 3.5},
        ],
    )

    matrix = build_feature_matrix(
        discovery_input,
        registry,
        ["numeric_value", "integer_count"],
        max_rows=2,
    )

    assert matrix.feature_names == ("integer_count", "numeric_value")
    assert matrix.values == ((2.0, 1.5), (4.0, 3.5))
    assert matrix.row_ids == (
        "BTCUSDT|1m|2025-01-01T00:00:00Z",
        "BTCUSDT|1m|2025-01-01T00:01:00Z",
    )
    assert matrix.dropped_null_rows == 0
    assert matrix.partition_role is PartitionRole.DISCOVERY


def test_matrix_drops_whole_rows_with_selected_nulls_and_counts_them() -> None:
    registry = _registry()
    discovery_input = _input(
        registry,
        [
            {"category_state": None, "integer_count": 1, "numeric_value": 2.0},
            {"category_state": "a", "integer_count": None, "numeric_value": 3.0},
            {"category_state": "b", "integer_count": 4, "numeric_value": None},
            {"category_state": None, "integer_count": 5, "numeric_value": 6.0},
        ],
    )

    matrix = build_feature_matrix(
        discovery_input,
        registry,
        ["integer_count", "numeric_value"],
        max_rows=4,
    )

    assert matrix.values == ((1.0, 2.0), (5.0, 6.0))
    assert matrix.dropped_null_rows == 2
    assert matrix.row_ids[1].endswith("00:03:00Z")


@pytest.mark.parametrize("max_rows", [0, -1, True, 1.5])
def test_matrix_rejects_invalid_row_cap(max_rows: object) -> None:
    registry = _registry()
    discovery_input = _input(
        registry,
        [{"category_state": "a", "integer_count": 1, "numeric_value": 2.0}],
    )

    with pytest.raises(ValueError, match="max_rows"):
        build_feature_matrix(
            discovery_input,
            registry,
            ["integer_count"],
            max_rows=max_rows,  # type: ignore[arg-type]
        )


def test_matrix_applies_cap_before_null_dropping() -> None:
    registry = _registry()
    discovery_input = _input(
        registry,
        [
            {"category_state": "a", "integer_count": None, "numeric_value": 2.0},
            {"category_state": "b", "integer_count": 2, "numeric_value": 3.0},
        ],
    )

    with pytest.raises(ValueError, match="exceeds max_rows"):
        build_feature_matrix(discovery_input, registry, ["integer_count"], max_rows=1)


@pytest.mark.parametrize(
    ("names", "message"),
    [
        ([], "at least one"),
        (["integer_count", "integer_count"], "unique"),
        (["missing_feature"], "registered"),
        (["category_state"], "numeric"),
        (["future_return"], "registered"),
    ],
)
def test_matrix_rejects_unsafe_feature_selection(names: list[str], message: str) -> None:
    registry = _registry()
    discovery_input = _input(
        registry,
        [{"category_state": "a", "integer_count": 1, "numeric_value": 2.0}],
    )

    with pytest.raises(ValueError, match=message):
        build_feature_matrix(discovery_input, registry, names, max_rows=1)


def test_matrix_rejects_registry_identity_mismatch() -> None:
    registry = _registry()
    discovery_input = _input(
        registry,
        [{"category_state": "a", "integer_count": 1, "numeric_value": 2.0}],
    )
    other_registry = _registry("FS-000402")

    with pytest.raises(ValueError, match="identity"):
        build_feature_matrix(discovery_input, other_registry, ["integer_count"], max_rows=1)


def test_matrix_rejects_empty_result_after_null_dropping() -> None:
    registry = _registry()
    discovery_input = _input(
        registry,
        [{"category_state": "a", "integer_count": None, "numeric_value": 2.0}],
    )

    with pytest.raises(ValueError, match="no complete rows"):
        build_feature_matrix(discovery_input, registry, ["integer_count"], max_rows=1)
