"""Versioned price-to-integer-bin definitions."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_FLOOR, localcontext
from math import isfinite, nextafter, ulp
from typing import Protocol, cast, runtime_checkable


@runtime_checkable
class BinDefinition(Protocol):
    """Caller-owned bins with rollback and an immutable historical definition."""

    @property
    def version(self) -> str: ...

    @property
    def definition_id(self) -> str: ...

    def bin_index(self, price: float) -> int: ...

    def price_for_index(self, index: int) -> float: ...

    def transaction_checkpoint(self) -> object: ...

    def restore_transaction(self, checkpoint: object) -> None: ...

    def snapshot_definition(self) -> BinDefinition: ...


class _StatelessBinTransaction:
    """No-op transaction contract for immutable built-in bin definitions."""

    def transaction_checkpoint(self) -> None:
        return None

    def restore_transaction(self, checkpoint: object) -> None:
        if checkpoint is not None:
            raise ValueError("stateless bin checkpoint must be None")

    def snapshot_definition(self) -> BinDefinition:
        """Return this immutable built-in definition for historical snapshots."""

        return cast(BinDefinition, self)


def _positive_finite(value: float, *, name: str) -> None:
    if not isfinite(value) or value <= 0:
        raise ValueError(f"{name} must be a positive finite number")


def _finite(value: float, *, name: str) -> None:
    if not isfinite(value):
        raise ValueError(f"{name} must be finite")


def _decimal(value: float) -> Decimal:
    return Decimal(str(value))


def _canonical(value: float) -> str:
    normalized = _decimal(value).normalize()
    return format(normalized, "f")


def _decimal_lower_boundary_float(value: Decimal) -> float:
    """Return a float whose shortest decimal is not below an exact boundary."""
    candidate = float(value)
    if not isfinite(candidate):
        raise OverflowError("bin price is outside the finite float range")
    if _decimal(candidate) < value:
        candidate = nextafter(candidate, float("inf"))
    return candidate


@dataclass(frozen=True)
class FixedStepBins(_StatelessBinTransaction):
    """Linear bins anchored at ``origin`` with deterministic decimal flooring."""

    step: float
    origin: float = 0.0
    version: str = "fixed-step-v1"
    provenance: str | None = None

    def __post_init__(self) -> None:
        _positive_finite(self.step, name="step")
        _finite(self.origin, name="origin")
        if not self.version:
            raise ValueError("version must not be empty")

    @property
    def definition_id(self) -> str:
        identity = f"{self.version}:step={_canonical(self.step)}:origin={_canonical(self.origin)}"
        if self.provenance is not None:
            return f"{identity}:source={self.provenance}"
        return identity

    def bin_index(self, price: float) -> int:
        _finite(price, name="price")
        quotient = (_decimal(price) - _decimal(self.origin)) / _decimal(self.step)
        return int(quotient.to_integral_value(rounding=ROUND_FLOOR))

    def price_for_index(self, index: int) -> float:
        if not isinstance(index, int):
            raise TypeError("index must be an integer")
        return _decimal_lower_boundary_float(_decimal(self.origin) + _decimal(self.step) * index)

    # Short aliases are convenient for algorithms that operate only in bin space.
    index = bin_index
    price = price_for_index


@dataclass(frozen=True)
class TickSizeBins(_StatelessBinTransaction):
    """Exchange-tick bin definition with explicit provenance."""

    tick_size: float
    origin: float = 0.0
    version: str = "exchange-tick-v1"
    provenance: str | None = None

    def __post_init__(self) -> None:
        _positive_finite(self.tick_size, name="tick_size")
        _finite(self.origin, name="origin")
        if not self.version:
            raise ValueError("version must not be empty")

    @property
    def step(self) -> float:
        return self.tick_size

    @property
    def definition_id(self) -> str:
        identity = (
            f"{self.version}:tick={_canonical(self.tick_size)}:origin={_canonical(self.origin)}"
        )
        if self.provenance is not None:
            return f"{identity}:source={self.provenance}"
        return identity

    def bin_index(self, price: float) -> int:
        return FixedStepBins(self.tick_size, self.origin).bin_index(price)

    def price_for_index(self, index: int) -> float:
        return FixedStepBins(self.tick_size, self.origin).price_for_index(index)

    index = bin_index
    price = price_for_index


@dataclass(frozen=True)
class LogPriceBins(_StatelessBinTransaction):
    """Constant-percentage bins represented by their lower price boundary."""

    percentage: float
    anchor_price: float = 1.0
    version: str = "log-price-v1"

    def __post_init__(self) -> None:
        _positive_finite(self.percentage, name="percentage")
        _positive_finite(self.anchor_price, name="anchor_price")
        if not self.version:
            raise ValueError("version must not be empty")

    @property
    def definition_id(self) -> str:
        return (
            f"{self.version}:percentage={_canonical(self.percentage)}:"
            f"anchor={_canonical(self.anchor_price)}"
        )

    def bin_index(self, price: float) -> int:
        _positive_finite(price, name="price")
        with localcontext() as context:
            context.prec = 60
            raw_index = (_decimal(price) / _decimal(self.anchor_price)).ln() / (
                Decimal(1) + _decimal(self.percentage)
            ).ln()
            return int(raw_index.to_integral_value(rounding=ROUND_FLOOR))

    def price_for_index(self, index: int) -> float:
        if not isinstance(index, int):
            raise TypeError("index must be an integer")
        with localcontext() as context:
            context.prec = 60
            value = _decimal(self.anchor_price) * (Decimal(1) + _decimal(self.percentage)) ** index
        return _decimal_lower_boundary_float(value)

    index = bin_index
    price = price_for_index


@dataclass(frozen=True)
class TargetCountBins(_StatelessBinTransaction):
    """Linear bins whose declared low/high bounds map to the first/last index."""

    low: float
    high: float
    target_count: int
    version: str = "target-count-v1"

    def __post_init__(self) -> None:
        _finite(self.low, name="low")
        _finite(self.high, name="high")
        if self.high <= self.low:
            raise ValueError("high must be greater than low")
        if isinstance(self.target_count, bool) or not isinstance(self.target_count, int):
            raise TypeError("target_count must be an integer")
        if self.target_count < 2:
            raise ValueError("target_count must be at least 2")
        if not self.version:
            raise ValueError("version must not be empty")
        with localcontext() as context:
            context.prec = 60
            exact_step = (_decimal(self.high) - _decimal(self.low)) / Decimal(self.target_count - 1)
        public_resolution = max(ulp(self.low), ulp(self.high))
        if exact_step < Decimal.from_float(public_resolution):
            raise ValueError(
                "target_count exceeds public float resolution for the configured range"
            )

    @property
    def step(self) -> float:
        return (self.high - self.low) / (self.target_count - 1)

    @property
    def definition_id(self) -> str:
        return (
            f"{self.version}:range={_canonical(self.low)}..{_canonical(self.high)}:"
            f"count={self.target_count}"
        )

    def bin_index(self, price: float) -> int:
        _finite(price, name="price")
        with localcontext() as context:
            context.prec = 60
            raw_index = (
                (_decimal(price) - _decimal(self.low))
                * Decimal(self.target_count - 1)
                / (_decimal(self.high) - _decimal(self.low))
            )
            return int(raw_index.to_integral_value(rounding=ROUND_FLOOR))

    def price_for_index(self, index: int) -> float:
        if not isinstance(index, int):
            raise TypeError("index must be an integer")
        with localcontext() as context:
            context.prec = 60
            value = _decimal(self.low) + (
                (_decimal(self.high) - _decimal(self.low))
                * Decimal(index)
                / Decimal(self.target_count - 1)
            )
        return _decimal_lower_boundary_float(value)

    index = bin_index
    price = price_for_index


def target_count_bins(
    *, low: float, high: float, target_count: int, version: str = "target-count-v1"
) -> TargetCountBins:
    """Create a linear definition whose inclusive bounds contain ``target_count`` bins."""
    return TargetCountBins(low=low, high=high, target_count=target_count, version=version)


def volatility_scaled_bins(
    *,
    reference_price: float,
    volatility: float,
    multiplier: float = 1.0,
    origin: float = 0.0,
    minimum_step: float | None = None,
    version: str = "volatility-scaled-v1",
) -> FixedStepBins:
    """Create fixed bins scaled by a frozen price and volatility estimate."""
    _positive_finite(reference_price, name="reference_price")
    _positive_finite(volatility, name="volatility")
    _positive_finite(multiplier, name="multiplier")
    if minimum_step is not None:
        _positive_finite(minimum_step, name="minimum_step")
    step = reference_price * volatility * multiplier
    if minimum_step is not None:
        step = max(step, minimum_step)
    provenance = (
        f"reference={_canonical(reference_price)}:volatility={_canonical(volatility)}:"
        f"multiplier={_canonical(multiplier)}"
    )
    return FixedStepBins(step=step, origin=origin, version=version, provenance=provenance)
