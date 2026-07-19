from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from market_structure_lab.discovery import (
    MissingnessPolicy,
    MissingnessPolicyViolation,
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


def _definition(
    name: str,
    kind: FeatureValueKind,
    *,
    required_prior_observations: int = 0,
) -> FeatureDefinition:
    return FeatureDefinition(
        name=name,
        definition=f"Test definition for {name}",
        family=FeatureFamily.SEQUENCE,
        value_kind=kind,
        units="ratio",
        required_prior_observations=required_prior_observations,
        missing_policy=MissingPolicy.NULL,
        version="1.0.0",
        leakage_class=(
            LeakageClass.AT_CUTOFF
            if required_prior_observations == 0
            else LeakageClass.TRAILING_ONLY
        ),
        source_fields=(f"fixture.{name}",),
        trailing_window=(
            "current_observation"
            if required_prior_observations == 0
            else f"trailing_{required_prior_observations + 1}_observations"
        ),
        observable_cutoff_rule=(
            ObservableCutoffRule.AT_INFORMATION_CUTOFF
            if required_prior_observations == 0
            else ObservableCutoffRule.TRAILING_THROUGH_INFORMATION_CUTOFF
        ),
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
    "changes",
    [
        {"maximum_total_drop_fraction": -0.1},
        {"maximum_total_drop_fraction": float("nan")},
        {"maximum_per_feature_drop_fraction": 1.1},
        {"maximum_evidence_groups": 0},
        {"maximum_evidence_groups": True},
    ],
)
def test_missingness_policy_rejects_invalid_bounds(changes: dict[str, object]) -> None:
    values: dict[str, object] = {
        "policy_id": "software-missingness-validation-v1",
        "maximum_total_drop_fraction": 0.5,
        "maximum_per_feature_drop_fraction": 0.5,
        "maximum_evidence_groups": 8,
    }
    values.update(changes)

    with pytest.raises(ValueError, match="maximum"):
        MissingnessPolicy(**values)  # type: ignore[arg-type]


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


def test_matrix_publishes_bounded_structural_missingness_evidence() -> None:
    registry = FeatureRegistry(
        "FS-000403",
        [
            _definition(
                "integer_count",
                FeatureValueKind.INTEGER,
                required_prior_observations=1,
            ),
            _definition("numeric_value", FeatureValueKind.FLOAT),
        ],
    )
    discovery_input = _input(
        registry,
        [
            {"integer_count": None, "numeric_value": 1.0},
            {"integer_count": 2, "numeric_value": None},
            {"integer_count": 3, "numeric_value": 3.0},
        ],
    )
    policy = MissingnessPolicy(
        policy_id="software-missingness-v1",
        maximum_total_drop_fraction=0.75,
        maximum_per_feature_drop_fraction=0.5,
        maximum_evidence_groups=8,
    )

    matrix = build_feature_matrix(
        discovery_input,
        registry,
        ("integer_count", "numeric_value"),
        max_rows=3,
        missingness_policy=policy,
    )

    evidence = matrix.missingness_evidence
    assert evidence is not None
    assert evidence.policy_sha256 == policy.sha256
    assert evidence.input_row_count == 3
    assert evidence.selected_row_count == 1
    assert evidence.excluded_row_count == 2
    assert evidence.selected_row_ids_sha256 != evidence.excluded_row_ids_sha256
    assert evidence.per_feature_dropped == (("integer_count", 1), ("numeric_value", 1))
    assert tuple(
        (
            group.feature_name,
            group.asset,
            group.partition_role,
            group.missingness_kind,
            group.auction_state,
            group.continuity_state,
            group.count,
        )
        for group in evidence.groups
    ) == (
        (
            "integer_count",
            "BTCUSDT",
            PartitionRole.DISCOVERY,
            "warm_up",
            "unavailable",
            "stream_start",
            1,
        ),
        (
            "numeric_value",
            "BTCUSDT",
            PartitionRole.DISCOVERY,
            "structural_null",
            "unavailable",
            "continuous",
            1,
        ),
    )


def test_matrix_rejects_frozen_missingness_threshold_before_model_work() -> None:
    registry = _registry()
    discovery_input = _input(
        registry,
        [
            {"category_state": "a", "integer_count": None, "numeric_value": 1.0},
            {"category_state": "a", "integer_count": 2, "numeric_value": None},
            {"category_state": "a", "integer_count": 3, "numeric_value": 3.0},
        ],
    )
    policy = MissingnessPolicy(
        policy_id="software-missingness-reject-v1",
        maximum_total_drop_fraction=0.5,
        maximum_per_feature_drop_fraction=0.5,
        maximum_evidence_groups=8,
    )

    with pytest.raises(MissingnessPolicyViolation, match="total drop fraction") as raised:
        build_feature_matrix(
            discovery_input,
            registry,
            ("integer_count", "numeric_value"),
            max_rows=3,
            missingness_policy=policy,
        )

    assert raised.value.evidence.excluded_row_count == 2


def test_matrix_enforces_per_feature_drop_fraction_independently() -> None:
    registry = _registry()
    discovery_input = _input(
        registry,
        [
            {"category_state": "a", "integer_count": None, "numeric_value": 1.0},
            {"category_state": "a", "integer_count": 2, "numeric_value": 2.0},
            {"category_state": "a", "integer_count": 3, "numeric_value": 3.0},
            {"category_state": "a", "integer_count": 4, "numeric_value": 4.0},
        ],
    )

    with pytest.raises(MissingnessPolicyViolation, match="integer_count.*drop fraction"):
        build_feature_matrix(
            discovery_input,
            registry,
            ("integer_count", "numeric_value"),
            max_rows=4,
            missingness_policy=MissingnessPolicy(
                policy_id="software-missingness-per-feature-v1",
                maximum_total_drop_fraction=0.5,
                maximum_per_feature_drop_fraction=0.2,
                maximum_evidence_groups=8,
            ),
        )


def test_matrix_rejects_excess_missingness_evidence_groups() -> None:
    registry = _registry()
    discovery_input = _input(
        registry,
        [
            {"category_state": "a", "integer_count": None, "numeric_value": 1.0},
            {"category_state": "a", "integer_count": 2, "numeric_value": None},
            {"category_state": "a", "integer_count": 3, "numeric_value": 3.0},
        ],
    )

    with pytest.raises(MissingnessPolicyViolation, match="evidence groups"):
        build_feature_matrix(
            discovery_input,
            registry,
            ("integer_count", "numeric_value"),
            max_rows=3,
            missingness_policy=MissingnessPolicy(
                policy_id="software-missingness-bounded-v1",
                maximum_total_drop_fraction=1.0,
                maximum_per_feature_drop_fraction=1.0,
                maximum_evidence_groups=1,
            ),
        )


def test_missingness_groups_bind_asset_period_auction_and_reset_state() -> None:
    registry = FeatureRegistry(
        "FS-000404",
        [
            _definition("auction_location", FeatureValueKind.CATEGORY),
            _definition(
                "numeric_value",
                FeatureValueKind.FLOAT,
                required_prior_observations=1,
            ),
        ],
    )
    start = datetime(2025, 1, 2, tzinfo=UTC)
    partition = TimePartition(
        role=PartitionRole.DEVELOPMENT,
        start=start,
        end=start + timedelta(hours=1),
        symbols=("BTCUSDT", "ETHUSDT"),
    )

    def row(
        *,
        minute: int,
        symbol: str,
        segment_id: int,
        auction_location: str,
        numeric_value: float | None,
    ) -> FeatureRow:
        timestamp = start + timedelta(minutes=minute)
        return FeatureRow(
            timestamp=timestamp,
            information_cutoff=timestamp + timedelta(minutes=1),
            symbol=symbol,
            timeframe="1m",
            segment_id=segment_id,
            dataset_version="DS-TEST",
            config_version="cfg-1",
            profile_version="profile-1",
            window_policy_id="window-1",
            feature_set_id=registry.feature_set_id,
            registry_id=registry.registry_id,
            values={
                "auction_location": auction_location,
                "numeric_value": numeric_value,
            },
        )

    development = make_discovery_input(
        partition=partition,
        rows=(
            row(
                minute=0,
                symbol="BTCUSDT",
                segment_id=0,
                auction_location="a",
                numeric_value=None,
            ),
            row(
                minute=2,
                symbol="BTCUSDT",
                segment_id=0,
                auction_location="b",
                numeric_value=None,
            ),
            row(
                minute=3,
                symbol="BTCUSDT",
                segment_id=1,
                auction_location="a",
                numeric_value=None,
            ),
            row(
                minute=4,
                symbol="BTCUSDT",
                segment_id=1,
                auction_location="a",
                numeric_value=1.0,
            ),
            row(
                minute=0,
                symbol="ETHUSDT",
                segment_id=0,
                auction_location="b",
                numeric_value=None,
            ),
            row(
                minute=1,
                symbol="ETHUSDT",
                segment_id=0,
                auction_location="b",
                numeric_value=2.0,
            ),
        ),
        registry=registry,
        purpose="stability",
        max_rows=6,
    )

    matrix = build_feature_matrix(
        development,
        registry,
        ("numeric_value",),
        max_rows=6,
        missingness_policy=MissingnessPolicy(
            policy_id="software-missingness-dimensional-v1",
            maximum_total_drop_fraction=0.75,
            maximum_per_feature_drop_fraction=0.75,
            maximum_evidence_groups=8,
        ),
    )

    evidence = matrix.missingness_evidence
    assert evidence is not None
    assert {(group.asset, group.partition_role) for group in evidence.groups} == {
        ("BTCUSDT", PartitionRole.DEVELOPMENT),
        ("ETHUSDT", PartitionRole.DEVELOPMENT),
    }
    assert {group.auction_state for group in evidence.groups} == {"a", "b"}
    assert {group.continuity_state for group in evidence.groups} == {
        "stream_start",
        "material_gap",
        "segment_reset",
    }
    assert {group.missingness_kind for group in evidence.groups} == {"warm_up"}
