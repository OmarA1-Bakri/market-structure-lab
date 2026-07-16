from __future__ import annotations

import json
import math
from dataclasses import FrozenInstanceError, replace
from datetime import UTC, datetime, timedelta, timezone
from types import MappingProxyType

import pytest

from market_structure_lab.features.models import FeatureRow
from market_structure_lab.features.normalization import (
    PartitionRole,
    RobustNormalizer,
    TrainingPartition,
    fit_robust_normalizer,
)
from market_structure_lab.features.registry import (
    FeatureDefinition,
    FeatureFamily,
    FeatureRegistry,
    FeatureValueKind,
    LeakageClass,
    MissingPolicy,
)


START = datetime(2025, 1, 1, tzinfo=UTC)


def _definition(
    name: str,
    kind: FeatureValueKind,
    *,
    missing_policy: MissingPolicy = MissingPolicy.NULL,
) -> FeatureDefinition:
    return FeatureDefinition(
        name=name,
        definition=f"Test definition for {name}",
        family=FeatureFamily.SEQUENCE,
        value_kind=kind,
        units="ratio",
        required_prior_observations=0,
        missing_policy=missing_policy,
        version="1.0.0",
        leakage_class=LeakageClass.TRAILING_ONLY,
        allowed_categories=("cold", "hot") if kind is FeatureValueKind.CATEGORY else (),
    )


@pytest.fixture
def registry() -> FeatureRegistry:
    return FeatureRegistry(
        "FS-000001",
        (
            _definition("constant", FeatureValueKind.FLOAT),
            _definition("count", FeatureValueKind.INTEGER),
            _definition("regime", FeatureValueKind.CATEGORY),
            _definition("signal", FeatureValueKind.FLOAT),
        ),
    )


def _partition(role: PartitionRole = PartitionRole.TRAINING) -> TrainingPartition:
    return TrainingPartition(
        split_id="walk-forward-v1.train",
        start=START,
        end=START + timedelta(days=1),
        role=role,
    )


def _row(
    registry: FeatureRegistry,
    minute: int,
    *,
    signal: float | None,
    constant: float | None = 7.0,
    count: int | None = 2,
    regime: str | None = "cold",
    dataset_version: str = "dataset-v1",
) -> FeatureRow:
    timestamp = START + timedelta(minutes=minute)
    return FeatureRow(
        timestamp=timestamp,
        information_cutoff=timestamp + timedelta(minutes=1),
        symbol="BTCUSDT",
        timeframe="1m",
        segment_id=1,
        dataset_version=dataset_version,
        config_version="config-v1",
        profile_version="profile-v1",
        window_policy_id="rolling-60-v1",
        feature_set_id=registry.feature_set_id,
        registry_id=registry.registry_id,
        values={
            "constant": constant,
            "count": count,
            "regime": regime,
            "signal": signal,
        },
    )


def test_linear_quantiles_and_transformation_match_fixture(registry: FeatureRegistry) -> None:
    rows = [_row(registry, index, signal=value) for index, value in enumerate((1.0, 2.0, 3.0, 8.0))]

    normalizer = fit_robust_normalizer(
        rows,
        registry,
        _partition(),
        "dataset-v1",
        selected_features=("signal",),
    )

    assert normalizer.medians == {"signal": 2.5}
    assert normalizer.iqrs == {"signal": 2.5}
    assert normalizer.scales == {"signal": 2.5}
    assert normalizer.fit_row_count == 4
    assert normalizer.transform_values(_row(registry, 10, signal=5.0)) == {"signal": 1.0}


def test_external_runs_are_bounded_and_match_exact_in_memory_quantiles(
    registry: FeatureRegistry,
) -> None:
    max_rows_per_run = 3
    signals = [
        None if index % 19 == 0 else float(((index * 97) % 211) - 105) + (index % 7) / 10
        for index in range(257)
    ]
    expected_values = sorted(value for value in signals if value is not None)

    def quantile(probability: float) -> float:
        position = (len(expected_values) - 1) * probability
        lower = math.floor(position)
        upper = math.ceil(position)
        if lower == upper:
            return expected_values[lower]
        weight = position - lower
        return expected_values[lower] * (1.0 - weight) + expected_values[upper] * weight

    normalizer = fit_robust_normalizer(
        [_row(registry, index, signal=value) for index, value in enumerate(signals)],
        registry,
        _partition(),
        "dataset-v1",
        selected_features=("signal",),
        max_rows_per_run=max_rows_per_run,
    )

    expected_q25 = quantile(0.25)
    expected_median = quantile(0.5)
    expected_q75 = quantile(0.75)
    assert normalizer.medians == {"signal": expected_median}
    assert normalizer.iqrs == {"signal": expected_q75 - expected_q25}
    assert normalizer.fit_row_count == len(signals)
    assert normalizer.max_rows_per_run == max_rows_per_run
    assert normalizer.max_buffered_rows == max_rows_per_run
    assert RobustNormalizer.from_json(normalizer.canonical_json()).canonical_json() == (
        normalizer.canonical_json()
    )


def test_default_selection_is_numeric_sorted_and_output_is_immutable(
    registry: FeatureRegistry,
) -> None:
    normalizer = fit_robust_normalizer(
        [_row(registry, 0, signal=1.0), _row(registry, 1, signal=3.0)],
        registry,
        _partition(),
        "dataset-v1",
    )

    assert normalizer.selected_features == ("constant", "count", "signal")
    transformed = normalizer.transform_values(_row(registry, 3, signal=2.0))
    assert isinstance(transformed, MappingProxyType)
    assert tuple(transformed) == normalizer.selected_features
    with pytest.raises(TypeError):
        transformed["signal"] = 99.0  # type: ignore[index]


def test_constant_scale_is_one_and_null_remains_null(registry: FeatureRegistry) -> None:
    normalizer = fit_robust_normalizer(
        [_row(registry, 0, signal=1.0), _row(registry, 1, signal=2.0)],
        registry,
        _partition(),
        "dataset-v1",
        selected_features=("constant",),
    )

    assert normalizer.iqrs == {"constant": 0.0}
    assert normalizer.scales == {"constant": 1.0}
    assert normalizer.transform_values(_row(registry, 2, signal=3.0, constant=None)) == {
        "constant": None
    }


@pytest.mark.parametrize("role", [PartitionRole.VALIDATION, PartitionRole.HOLDOUT])
def test_fit_rejects_nontraining_partition(registry: FeatureRegistry, role: PartitionRole) -> None:
    with pytest.raises(ValueError, match="training"):
        fit_robust_normalizer(
            [_row(registry, 0, signal=1.0)], registry, _partition(role), "dataset-v1"
        )


def test_holdout_values_cannot_poison_training_fit(registry: FeatureRegistry) -> None:
    training = [_row(registry, 0, signal=1.0), _row(registry, 1, signal=3.0)]
    fitted = fit_robust_normalizer(
        training,
        registry,
        _partition(),
        "dataset-v1",
        selected_features=("signal",),
    )
    before = fitted.canonical_json()

    transformed = fitted.transform_values(_row(registry, 2_000, signal=1_000_000.0))

    assert transformed["signal"] == pytest.approx(999_998.0)
    assert fitted.medians == {"signal": 2.0}
    assert fitted.canonical_json() == before


def test_fit_rejects_cutoffs_outside_half_open_bounds(registry: FeatureRegistry) -> None:
    at_end = _row(registry, (24 * 60) - 1, signal=1.0)
    before_start = _row(registry, -2, signal=1.0)

    for invalid in (at_end, before_start):
        with pytest.raises(ValueError, match="inside"):
            fit_robust_normalizer(
                [invalid], registry, _partition(), "dataset-v1", selected_features=("signal",)
            )


def test_fit_rejects_mixed_identity_rows(registry: FeatureRegistry) -> None:
    valid = _row(registry, 0, signal=1.0)
    cases = (
        replace(valid, dataset_version="dataset-v2"),
        replace(valid, feature_set_id="FS-999999"),
        replace(valid, registry_id="FR-000000000000"),
    )

    for invalid in cases:
        with pytest.raises(ValueError, match="does not match"):
            fit_robust_normalizer([valid, invalid], registry, _partition(), "dataset-v1")


def test_fit_rejects_category_unknown_duplicate_and_empty_observations(
    registry: FeatureRegistry,
) -> None:
    row = _row(registry, 0, signal=None)

    for selected, message in (
        (("regime",), "categorical"),
        (("unknown",), "unknown"),
        (("signal", "signal"), "duplicates"),
    ):
        with pytest.raises(ValueError, match=message):
            fit_robust_normalizer(
                [row], registry, _partition(), "dataset-v1", selected_features=selected
            )
    with pytest.raises(ValueError, match="no non-null"):
        fit_robust_normalizer(
            [row], registry, _partition(), "dataset-v1", selected_features=("signal",)
        )


def test_fit_rejects_empty_rows_and_invalid_registry_row(registry: FeatureRegistry) -> None:
    with pytest.raises(ValueError, match="without training rows"):
        fit_robust_normalizer([], registry, _partition(), "dataset-v1")

    invalid = _row(registry, 0, signal=1.0)
    invalid = replace(invalid, values={**invalid.values, "signal": "bad"})
    with pytest.raises(TypeError, match="float"):
        fit_robust_normalizer([invalid], registry, _partition(), "dataset-v1")


@pytest.mark.parametrize("limit", [0, -1, True, 1.5])
def test_fit_rejects_invalid_run_bound(registry: FeatureRegistry, limit: object) -> None:
    with pytest.raises(ValueError, match="max_rows_per_run"):
        fit_robust_normalizer(
            [_row(registry, 0, signal=1.0)],
            registry,
            _partition(),
            "dataset-v1",
            max_rows_per_run=limit,  # type: ignore[arg-type]
        )


def test_json_is_byte_stable_round_trips_and_detects_tampering(
    registry: FeatureRegistry,
) -> None:
    normalizer = fit_robust_normalizer(
        [_row(registry, 0, signal=1.0), _row(registry, 1, signal=3.0)],
        registry,
        _partition(),
        "dataset-v1",
        selected_features=("signal", "constant"),
    )

    encoded = normalizer.canonical_json()
    restored = RobustNormalizer.from_json(encoded)
    assert restored.canonical_json() == encoded
    assert restored.artifact_sha256 == normalizer.artifact_sha256
    assert json.loads(encoded)["artifact_sha256"] == normalizer.artifact_sha256

    tampered = json.loads(encoded)
    tampered["medians"]["signal"] = 999.0
    with pytest.raises(ValueError, match="SHA-256 mismatch"):
        RobustNormalizer.from_json(json.dumps(tampered))


def test_partition_requires_safe_id_utc_and_valid_bounds() -> None:
    with pytest.raises(ValueError, match="split_id"):
        replace(_partition(), split_id="unsafe/path")
    with pytest.raises(ValueError, match="timezone-aware"):
        replace(_partition(), start=START.replace(tzinfo=None))
    with pytest.raises(ValueError, match="must use UTC"):
        replace(_partition(), start=START.astimezone(timezone(timedelta(hours=7))))
    with pytest.raises(ValueError, match="precede"):
        replace(_partition(), start=START + timedelta(days=1))


def test_partition_is_frozen() -> None:
    partition = _partition()
    assert partition.start.tzinfo is UTC
    with pytest.raises(FrozenInstanceError):
        partition.split_id = "split-v2"  # type: ignore[misc]


def test_transform_rejects_rows_from_other_artifact_identity(registry: FeatureRegistry) -> None:
    normalizer = fit_robust_normalizer(
        [_row(registry, 0, signal=1.0)],
        registry,
        _partition(),
        "dataset-v1",
        selected_features=("signal",),
    )

    with pytest.raises(ValueError, match="dataset version"):
        normalizer.transform_values(_row(registry, 2, signal=1.0, dataset_version="dataset-v2"))
