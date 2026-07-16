"""Immutable, outcome-blind market-event records."""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from types import MappingProxyType
from typing import Mapping, TypeAlias

from market_structure_lab.features.models import FeatureRow, FeatureValue
from market_structure_lab.features.registry import (
    FeatureRegistry,
    validate_discovery_field_name,
)

JsonScalar: TypeAlias = str | int | float | bool | None

_EVENT_ID = re.compile(r"^EV-[A-F0-9]{64}$")
_SHA256 = re.compile(r"^[a-f0-9]{64}$")
class EventKind(StrEnum):
    """Outcome-blind segment and structural-event categories."""

    FIXED_WINDOW = "fixed_window"
    ROLLING_WINDOW = "rolling_window"
    UTC_SESSION = "utc_session"
    CHANGE_POINT = "change_point"
    EXPANSION = "expansion"
    VALUE_EXIT = "value_exit"
    VALUE_REENTRY = "value_reentry"
    POC_MIGRATION = "poc_migration"
    NODE_TEST = "node_test"
    NODE_TRAVERSAL = "node_traversal"


def _require_utc(value: datetime, field: str) -> None:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field} must be UTC-aware")
    if value.utcoffset() != timedelta(0):
        raise ValueError(f"{field} must use UTC")


def _require_text(value: str, field: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be non-empty")


def _iso_utc(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _copy_feature_values(values: Mapping[str, FeatureValue]) -> Mapping[str, FeatureValue]:
    if not isinstance(values, Mapping):
        raise TypeError("feature_values must be a mapping")
    copied: dict[str, FeatureValue] = {}
    for name, value in sorted(values.items()):
        validate_discovery_field_name(name, field="feature value name")
        if value is not None and not isinstance(value, (float, int, str)):
            raise TypeError(f"feature {name!r} has an unsupported value type")
        if isinstance(value, float) and not math.isfinite(value):
            raise ValueError(f"feature {name!r} must be finite or null")
        copied[name] = value
    return MappingProxyType(copied)


def _copy_metadata(values: Mapping[str, JsonScalar]) -> Mapping[str, JsonScalar]:
    if not isinstance(values, Mapping):
        raise TypeError("metadata must be a mapping")
    copied: dict[str, JsonScalar] = {}
    for name, value in sorted(values.items()):
        validate_discovery_field_name(name, field="metadata name")
        if value is not None and not isinstance(value, (str, int, float, bool)):
            raise TypeError(f"metadata {name!r} must be a JSON scalar")
        if isinstance(value, float) and not math.isfinite(value):
            raise ValueError(f"metadata {name!r} must be finite or null")
        copied[name] = value
    return MappingProxyType(copied)


def _identity_payload(
    *,
    kind: EventKind,
    start: datetime,
    end: datetime,
    information_cutoff: datetime,
    symbol: str,
    timeframe: str,
    segment_id: int,
    dataset_version: str,
    config_version: str,
    profile_version: str,
    window_policy_id: str,
    feature_set_id: str,
    registry_id: str,
    registry_sha256: str,
    trigger_version: str,
    exploratory: bool,
    metadata: Mapping[str, JsonScalar],
    feature_values: Mapping[str, FeatureValue],
) -> dict[str, object]:
    feature_values_sha256 = hashlib.sha256(
        json.dumps(
            dict(feature_values),
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()
    return {
        "config_version": config_version,
        "dataset_version": dataset_version,
        "end": _iso_utc(end),
        "exploratory": exploratory,
        "feature_set_id": feature_set_id,
        "feature_values_sha256": feature_values_sha256,
        "information_cutoff": _iso_utc(information_cutoff),
        "kind": kind.value,
        "metadata": dict(metadata),
        "profile_version": profile_version,
        "registry_id": registry_id,
        "registry_sha256": registry_sha256,
        "segment_id": segment_id,
        "start": _iso_utc(start),
        "symbol": symbol,
        "timeframe": timeframe,
        "trigger_version": trigger_version,
        "window_policy_id": window_policy_id,
    }


def _event_id(**identity: object) -> str:
    payload = json.dumps(
        identity,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return f"EV-{hashlib.sha256(payload.encode('utf-8')).hexdigest().upper()}"


@dataclass(frozen=True, slots=True)
class MarketEvent:
    """A half-open event whose evidence is frozen at its exclusive end."""

    event_id: str
    kind: EventKind
    start: datetime
    end: datetime
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
    registry_sha256: str
    trigger_version: str
    exploratory: bool
    feature_values: Mapping[str, FeatureValue]
    metadata: Mapping[str, JsonScalar]

    def __post_init__(self) -> None:
        if not isinstance(self.kind, EventKind):
            raise TypeError("kind must be an EventKind")
        for timestamp_value, field in (
            (self.start, "start"),
            (self.end, "end"),
            (self.information_cutoff, "information_cutoff"),
        ):
            _require_utc(timestamp_value, field)
        if self.start >= self.end:
            raise ValueError("event interval must be a non-empty half-open range")
        if self.information_cutoff != self.end:
            raise ValueError("information_cutoff must equal the event's exclusive end")
        for text_value, field in (
            (self.symbol, "symbol"),
            (self.timeframe, "timeframe"),
            (self.dataset_version, "dataset_version"),
            (self.config_version, "config_version"),
            (self.profile_version, "profile_version"),
            (self.window_policy_id, "window_policy_id"),
            (self.feature_set_id, "feature_set_id"),
            (self.registry_id, "registry_id"),
            (self.trigger_version, "trigger_version"),
        ):
            _require_text(text_value, field)
        if not isinstance(self.registry_sha256, str) or _SHA256.fullmatch(
            self.registry_sha256
        ) is None:
            raise ValueError("registry_sha256 must be a lowercase SHA-256 digest")
        if isinstance(self.segment_id, bool) or not isinstance(self.segment_id, int):
            raise TypeError("segment_id must be a non-negative integer")
        if self.segment_id < 0:
            raise ValueError("segment_id must be a non-negative integer")
        if not isinstance(self.exploratory, bool):
            raise TypeError("exploratory must be a boolean")

        feature_values = _copy_feature_values(self.feature_values)
        metadata = _copy_metadata(self.metadata)
        object.__setattr__(self, "feature_values", feature_values)
        object.__setattr__(self, "metadata", metadata)

        expected_id = _event_id(
            **_identity_payload(
                kind=self.kind,
                start=self.start,
                end=self.end,
                information_cutoff=self.information_cutoff,
                symbol=self.symbol,
                timeframe=self.timeframe,
                segment_id=self.segment_id,
                dataset_version=self.dataset_version,
                config_version=self.config_version,
                profile_version=self.profile_version,
                window_policy_id=self.window_policy_id,
                feature_set_id=self.feature_set_id,
                registry_id=self.registry_id,
                registry_sha256=self.registry_sha256,
                trigger_version=self.trigger_version,
                exploratory=self.exploratory,
                metadata=metadata,
                feature_values=feature_values,
            )
        )
        if not isinstance(self.event_id, str) or _EVENT_ID.fullmatch(self.event_id) is None:
            raise ValueError("event_id must be an EV- prefixed SHA-256 digest")
        if self.event_id != expected_id:
            raise ValueError("event_id does not match the event identity")

    def to_dict(self) -> dict[str, object]:
        return {
            "event_id": self.event_id,
            "kind": self.kind.value,
            "start": _iso_utc(self.start),
            "end": _iso_utc(self.end),
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
            "registry_sha256": self.registry_sha256,
            "trigger_version": self.trigger_version,
            "exploratory": self.exploratory,
            "feature_values": dict(self.feature_values),
            "metadata": dict(self.metadata),
        }

    def canonical_json(self) -> str:
        return json.dumps(
            self.to_dict(),
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )


def make_event(
    kind: EventKind,
    start: datetime,
    end: datetime,
    row: FeatureRow,
    trigger_version: str,
    *,
    registry: FeatureRegistry,
    exploratory: bool = False,
    metadata: Mapping[str, JsonScalar] | None = None,
) -> MarketEvent:
    """Freeze an event at exactly the supplied feature row's information cutoff."""

    if not isinstance(row, FeatureRow):
        raise TypeError("row must be a FeatureRow")
    if not isinstance(registry, FeatureRegistry):
        raise TypeError("registry must be a FeatureRegistry")
    registry.validate_row(row)
    if row.information_cutoff != end:
        raise ValueError("event end must equal the feature row information_cutoff")
    copied_metadata = _copy_metadata(metadata or {})
    identity = _identity_payload(
        kind=kind,
        start=start,
        end=end,
        information_cutoff=row.information_cutoff,
        symbol=row.symbol,
        timeframe=row.timeframe,
        segment_id=row.segment_id,
        dataset_version=row.dataset_version,
        config_version=row.config_version,
        profile_version=row.profile_version,
        window_policy_id=row.window_policy_id,
        feature_set_id=row.feature_set_id,
        registry_id=row.registry_id,
        registry_sha256=registry.sha256,
        trigger_version=trigger_version,
        exploratory=exploratory,
        metadata=copied_metadata,
        feature_values=row.values,
    )
    return MarketEvent(
        event_id=_event_id(**identity),
        kind=kind,
        start=start,
        end=end,
        information_cutoff=row.information_cutoff,
        symbol=row.symbol,
        timeframe=row.timeframe,
        segment_id=row.segment_id,
        dataset_version=row.dataset_version,
        config_version=row.config_version,
        profile_version=row.profile_version,
        window_policy_id=row.window_policy_id,
        feature_set_id=row.feature_set_id,
        registry_id=row.registry_id,
        registry_sha256=registry.sha256,
        trigger_version=trigger_version,
        exploratory=exploratory,
        feature_values=row.values,
        metadata=copied_metadata,
    )
