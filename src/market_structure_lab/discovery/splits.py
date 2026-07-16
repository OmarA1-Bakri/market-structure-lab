"""Frozen chronological split policy and fail-closed discovery input guards."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Iterable, Literal, Mapping, Sequence

from market_structure_lab.features.models import FeatureRow
from market_structure_lab.features.registry import FeatureRegistry

_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@+-]{0,127}$")
_ROW_SOURCE_IDENTITY_FIELDS = ("config_version", "profile_version", "window_policy_id")


class PartitionRole(StrEnum):
    """Purpose assigned to a frozen chronological partition."""

    DISCOVERY = "discovery"
    DEVELOPMENT = "development"
    HOLDOUT = "holdout"


@dataclass(frozen=True, slots=True)
class TimePartition:
    """A UTC, half-open time interval over an explicit asset universe."""

    role: PartitionRole
    start: datetime
    end: datetime
    symbols: tuple[str, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.role, PartitionRole):
            raise TypeError("role must be a PartitionRole")
        start = _require_utc(self.start, "partition start")
        end = _require_utc(self.end, "partition end")
        if start >= end:
            raise ValueError("partition start must precede end")
        symbols = _canonical_names(self.symbols, "symbols", require_non_empty=True)
        object.__setattr__(self, "start", start)
        object.__setattr__(self, "end", end)
        object.__setattr__(self, "symbols", symbols)

    def contains(self, information_cutoff: datetime) -> bool:
        """Return whether a UTC information cutoff falls in this half-open interval."""

        cutoff = _require_utc(information_cutoff, "information_cutoff")
        return self.start <= cutoff < self.end

    def to_dict(self) -> dict[str, object]:
        return {
            "role": self.role.value,
            "start": _format_utc(self.start),
            "end": _format_utc(self.end),
            "symbols": list(self.symbols),
        }


@dataclass(frozen=True, slots=True)
class FrozenDiscoverySplit:
    """Canonical chronological split policy identified by its full SHA-256."""

    split_id: str
    discovery: TimePartition
    development: TimePartition
    holdout: TimePartition
    asset_holdouts: tuple[str, ...]
    sha256: str


@dataclass(frozen=True, slots=True)
class DiscoveryInput:
    """Validated, ordered feature rows admitted for one discovery purpose."""

    partition: TimePartition
    rows: tuple[FeatureRow, ...]
    dataset_version: str
    feature_set_id: str
    registry_id: str


def freeze_split(
    *,
    split_id: str,
    discovery: TimePartition,
    development: TimePartition,
    holdout: TimePartition,
    asset_holdouts: Sequence[str] = (),
) -> FrozenDiscoverySplit:
    """Validate and freeze chronological and asset holdouts into a canonical policy."""

    if not isinstance(split_id, str) or _SAFE_ID.fullmatch(split_id) is None:
        raise ValueError("split_id must be a safe, non-empty identifier")
    partitions = (discovery, development, holdout)
    if any(not isinstance(partition, TimePartition) for partition in partitions):
        raise TypeError("split partitions must be TimePartition values")
    expected_roles = (
        (discovery, PartitionRole.DISCOVERY, "discovery"),
        (development, PartitionRole.DEVELOPMENT, "development"),
        (holdout, PartitionRole.HOLDOUT, "holdout"),
    )
    for partition, role, label in expected_roles:
        if partition.role is not role:
            raise ValueError(f"{label} partition role must be {role.value}")
    if not (discovery.end <= development.start and development.end <= holdout.start):
        raise ValueError("split partitions must be chronological and non-overlapping")
    if not (discovery.symbols == development.symbols == holdout.symbols):
        raise ValueError("split partitions must use the same symbols")

    canonical_holdouts = _canonical_names(asset_holdouts, "asset_holdouts")
    if set(canonical_holdouts) & set(discovery.symbols):
        raise ValueError("asset holdouts must not appear in temporal partition symbols")
    payload = {
        "split_id": split_id,
        "discovery": discovery.to_dict(),
        "development": development.to_dict(),
        "holdout": holdout.to_dict(),
        "asset_holdouts": list(canonical_holdouts),
    }
    digest = hashlib.sha256(_canonical_json(payload)).hexdigest()
    return FrozenDiscoverySplit(
        split_id=split_id,
        discovery=discovery,
        development=development,
        holdout=holdout,
        asset_holdouts=canonical_holdouts,
        sha256=digest,
    )


def make_discovery_input(
    *,
    partition: TimePartition,
    rows: Iterable[FeatureRow],
    registry: FeatureRegistry,
    purpose: Literal["fit", "stability"],
    max_rows: int,
) -> DiscoveryInput:
    """Admit bounded, identity-consistent rows without ever reading holdout rows."""

    if not isinstance(partition, TimePartition):
        raise TypeError("partition must be a TimePartition")
    _guard_partition_role(partition, purpose)
    if isinstance(max_rows, bool) or not isinstance(max_rows, int) or max_rows < 1:
        raise ValueError("max_rows must be a positive integer")
    if not isinstance(registry, FeatureRegistry):
        raise TypeError("registry must be a FeatureRegistry")

    accepted: list[FeatureRow] = []
    dataset_version: str | None = None
    source_identity: tuple[str, ...] | None = None
    previous_key: tuple[str, str, datetime] | None = None
    for row in rows:
        if len(accepted) == max_rows:
            raise ValueError("discovery input exceeds max_rows")
        if not isinstance(row, FeatureRow):
            raise TypeError("rows must contain only FeatureRow values")
        registry.validate_row(row)
        if row.symbol not in partition.symbols:
            raise ValueError("feature row symbol is outside the partition symbols")
        if not partition.contains(row.information_cutoff):
            raise ValueError("feature row information cutoff is outside the partition")
        if dataset_version is None:
            dataset_version = row.dataset_version
        elif row.dataset_version != dataset_version:
            raise ValueError("feature row dataset version changed within discovery input")
        row_source_identity = tuple(getattr(row, field) for field in _ROW_SOURCE_IDENTITY_FIELDS)
        if source_identity is None:
            source_identity = row_source_identity
        elif row_source_identity != source_identity:
            changed = next(
                field
                for field, expected, actual in zip(
                    _ROW_SOURCE_IDENTITY_FIELDS,
                    source_identity,
                    row_source_identity,
                    strict=True,
                )
                if actual != expected
            )
            raise ValueError(f"feature row {changed} changed within discovery input")
        key = (row.symbol, row.timeframe, row.timestamp)
        if previous_key is not None and key < previous_key:
            raise ValueError("feature rows must be ordered by symbol, timeframe, and UTC timestamp")
        previous_key = key
        accepted.append(row)

    if dataset_version is None:
        raise ValueError("discovery input requires at least one feature row")
    return DiscoveryInput(
        partition=partition,
        rows=tuple(accepted),
        dataset_version=dataset_version,
        feature_set_id=registry.feature_set_id,
        registry_id=registry.registry_id,
    )


def _guard_partition_role(
    partition: TimePartition, purpose: Literal["fit", "stability"] | str
) -> None:
    if partition.role is PartitionRole.HOLDOUT:
        raise ValueError("final holdout data cannot be used for discovery input")
    if purpose not in ("fit", "stability"):
        raise ValueError("purpose must be 'fit' or 'stability'")
    if purpose == "fit" and partition.role is not PartitionRole.DISCOVERY:
        raise ValueError("fit purpose requires the discovery partition")
    if purpose == "stability" and partition.role not in (
        PartitionRole.DISCOVERY,
        PartitionRole.DEVELOPMENT,
    ):
        raise ValueError("stability purpose requires a discovery or development partition")


def _canonical_names(
    values: Sequence[str], label: str, *, require_non_empty: bool = False
) -> tuple[str, ...]:
    if isinstance(values, (str, bytes)):
        raise ValueError(f"{label} must be a sequence of names")
    names = tuple(values)
    if require_non_empty and not names:
        raise ValueError(f"{label} must be non-empty")
    if any(not isinstance(name, str) or not name.strip() for name in names):
        raise ValueError(f"{label} must contain non-empty strings")
    if len(names) != len(set(names)):
        raise ValueError(f"{label} must contain unique names")
    return tuple(sorted(names))


def _require_utc(value: datetime, label: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{label} must be UTC-aware")
    if value.utcoffset() != timedelta(0):
        raise ValueError(f"{label} must use UTC")
    return value.astimezone(UTC)


def _format_utc(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


def _canonical_json(value: Mapping[str, object]) -> bytes:
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
