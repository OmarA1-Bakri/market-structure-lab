from __future__ import annotations

import pytest

from market_structure_lab.profiles.binning import (
    FixedStepBins,
    LogPriceBins,
    TickSizeBins,
    target_count_bins,
    volatility_scaled_bins,
)


@pytest.mark.parametrize(
    ("price", "expected"),
    [
        (0.3, 3),
        (0.299999999999, 2),
        (-0.01, -1),
        (-0.1, -1),
    ],
)
def test_fixed_bins_use_deterministic_integer_indices(price: float, expected: int) -> None:
    bins = FixedStepBins(step=0.1)

    assert bins.bin_index(price) == expected
    assert isinstance(bins.bin_index(price), int)


@pytest.mark.parametrize("step", [0.1, 0.0001, 0.3333333333333333])
@pytest.mark.parametrize("index", [-10_000, -7, -1, 0, 1, 7, 10_000])
def test_fixed_bins_representative_prices_round_trip(step: float, index: int) -> None:
    bins = FixedStepBins(step=step)

    assert bins.bin_index(bins.price_for_index(index)) == index


def test_tick_bins_round_trip_decimal_prices() -> None:
    bins = TickSizeBins(tick_size=0.0001)

    assert bins.bin_index(bins.price_for_index(12_345)) == 12_345
    assert bins.price_for_index(12_345) == 1.2345


def test_log_bins_round_trip_representative_prices() -> None:
    bins = LogPriceBins(percentage=0.01, anchor_price=100.0)

    for index in (-200, -1, 0, 1, 200):
        assert bins.bin_index(bins.price_for_index(index)) == index


@pytest.mark.parametrize("percentage", [0.1, 0.01, 0.0001, 0.000001, 0.00000001])
@pytest.mark.parametrize("index", [-1_000, -16, -1, 0, 1, 4, 7, 16, 1_000])
def test_log_bins_round_trip_fine_percentages(percentage: float, index: int) -> None:
    bins = LogPriceBins(percentage=percentage, anchor_price=100.0)

    assert bins.bin_index(bins.price_for_index(index)) == index


@pytest.mark.parametrize(
    ("percentage", "anchor", "boundary", "expected"),
    [(0.0001, 1.0, 1.0001, 1), (0.01, 100.1, 101.101, 1)],
)
def test_log_bins_honor_declared_decimal_boundaries(
    percentage: float, anchor: float, boundary: float, expected: int
) -> None:
    assert LogPriceBins(percentage=percentage, anchor_price=anchor).bin_index(boundary) == expected


def test_target_count_factory_is_bounded_and_versioned() -> None:
    first = target_count_bins(low=100.0, high=200.0, target_count=11)
    second = target_count_bins(low=100.0, high=200.0, target_count=11)

    assert first.bin_index(100.0) == 0
    assert first.bin_index(200.0) == 10
    assert first.definition_id == second.definition_id
    assert first.version == "target-count-v1"


@pytest.mark.parametrize(
    ("low", "high", "count"),
    [(0.0, 0.1, 8), (0.3, 0.9, 17), (-0.3, 0.3, 11), (100.0, 200.0, 11)],
)
def test_target_count_bounds_and_representatives_round_trip(
    low: float, high: float, count: int
) -> None:
    bins = target_count_bins(low=low, high=high, target_count=count)

    assert bins.bin_index(low) == 0
    assert bins.bin_index(high) == count - 1
    assert [bins.bin_index(bins.price_for_index(index)) for index in range(count)] == list(
        range(count)
    )


@pytest.mark.parametrize("count", range(2, 65))
def test_target_count_dense_fractional_grid_round_trips(count: int) -> None:
    bins = target_count_bins(low=0.0, high=0.1, target_count=count)

    assert [bins.bin_index(bins.price_for_index(index)) for index in range(count)] == list(
        range(count)
    )


@pytest.mark.parametrize(
    "kwargs",
    [
        {"low": 100.0, "high": 100.0, "target_count": 10},
        {"low": 100.0, "high": 200.0, "target_count": 1},
    ],
)
def test_target_count_factory_rejects_invalid_inputs(kwargs: dict[str, float | int]) -> None:
    with pytest.raises(ValueError):
        target_count_bins(**kwargs)  # type: ignore[arg-type]


def test_target_count_factory_rejects_non_integer_counts() -> None:
    with pytest.raises(TypeError, match="integer"):
        target_count_bins(low=100.0, high=200.0, target_count=True)
    with pytest.raises(TypeError, match="integer"):
        target_count_bins(low=100.0, high=200.0, target_count=10.5)  # type: ignore[arg-type]


def test_target_count_factory_rejects_unrepresentable_public_boundaries() -> None:
    with pytest.raises(ValueError, match="float resolution"):
        target_count_bins(
            low=10_000_000_000_000_000.0,
            high=10_000_000_000_000_002.0,
            target_count=3,
        )


def test_volatility_factory_uses_positive_scaled_step_and_stable_identifier() -> None:
    bins = volatility_scaled_bins(
        reference_price=20_000.0,
        volatility=0.002,
        multiplier=0.5,
    )

    assert bins.step == 20.0
    assert bins.version == "volatility-scaled-v1"
    assert bins.definition_id == volatility_scaled_bins(
        reference_price=20_000.0,
        volatility=0.002,
        multiplier=0.5,
    ).definition_id


@pytest.mark.parametrize("value", [0.0, -1.0, float("inf"), float("nan")])
def test_bin_definitions_reject_non_positive_or_non_finite_configuration(value: float) -> None:
    with pytest.raises(ValueError):
        FixedStepBins(step=value)

    with pytest.raises(ValueError):
        LogPriceBins(percentage=value)
