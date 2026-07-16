"""Immutable, cutoff-bound rows for deterministic discovery features."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from types import MappingProxyType
from typing import Mapping, TypeAlias

from market_structure_lab.data.canonical import timeframe_microseconds

FeatureValue: TypeAlias = float | int | str | None


def timeframe_duration(timeframe: str) -> timedelta:
    """Return the exact duration represented by a canonical fixed timeframe."""

    return timedelta(microseconds=timeframe_microseconds(timeframe))


def _require_utc(value: datetime, field: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field} must be UTC-aware")
    if value.utcoffset() != timedelta(0):
        raise ValueError(f"{field} must use UTC")


def _iso_utc(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _require_text(value: str, field: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be non-empty")


@dataclass(frozen=True)
class FeatureRow:
    """A feature vector whose evidence is observable at ``information_cutoff``."""

    timestamp: datetime
    information_cutoff: datetime
    symbol: str
    timeframe: str
    segment_id: int
    dataset_version: str
    config_version: str
    profile_version: str
    window_policy_id: str
    feature_set_id: str
    registry_id: str
    values: Mapping[str, FeatureValue]

    def __post_init__(self) -> None:
        _require_utc(self.timestamp, "timestamp")
        _require_utc(self.information_cutoff, "information_cutoff")
        expected_cutoff = self.timestamp + timeframe_duration(self.timeframe)
        if self.information_cutoff != expected_cutoff:
            raise ValueError("information_cutoff must equal timestamp plus the timeframe duration")
        for field in (
            "symbol",
            "timeframe",
            "dataset_version",
            "config_version",
            "profile_version",
            "window_policy_id",
            "feature_set_id",
            "registry_id",
        ):
            _require_text(getattr(self, field), field)
        if isinstance(self.segment_id, bool) or not isinstance(self.segment_id, int):
            raise TypeError("segment_id must be a non-negative integer")
        if self.segment_id < 0:
            raise ValueError("segment_id must be a non-negative integer")
        if not isinstance(self.values, Mapping):
            raise TypeError("values must be a mapping")
        copied: dict[str, FeatureValue] = {}
        for name, value in sorted(self.values.items()):
            _require_text(name, "feature value name")
            if value is not None and not isinstance(value, (float, int, str)):
                raise TypeError(f"feature {name!r} has an unsupported value type")
            copied[name] = value
        object.__setattr__(self, "values", MappingProxyType(copied))

    def metadata_dict(self) -> dict[str, object]:
        """Return constructor-ready metadata without the feature vector."""

        return {
            "timestamp": self.timestamp,
            "information_cutoff": self.information_cutoff,
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "segment_id": self.segment_id,
            "dataset_version": self.dataset_version,
            "config_version": self.config_version,
            "profile_version": self.profile_version,
            "window_policy_id": self.window_policy_id,
            "feature_set_id": self.feature_set_id,
            "registry_id": self.registry_id,
        }

    def to_dict(self) -> dict[str, object]:
        return {
            "timestamp": _iso_utc(self.timestamp),
            "information_cutoff": _iso_utc(self.information_cutoff),
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "segment_id": self.segment_id,
            "dataset_version": self.dataset_version,
            "config_version": self.config_version,
            "profile_version": self.profile_version,
            "window_policy_id": self.window_policy_id,
            "feature_set_id": self.feature_set_id,
            "registry_id": self.registry_id,
            "values": dict(self.values),
        }

    def canonical_json(self) -> str:
        return json.dumps(
            self.to_dict(),
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
