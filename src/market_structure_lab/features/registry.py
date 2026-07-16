"""Versioned feature metadata and fail-closed discovery leakage audit."""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from enum import Enum
from typing import Iterable

from market_structure_lab.features.models import FeatureRow

_FEATURE_NAME = re.compile(r"^[a-z][a-z0-9]*(?:_[a-z0-9]+)*$")
_VERSION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_FEATURE_SET_ID = re.compile(r"^FS-[0-9]{6}$")
_PROHIBITED_DISCOVERY_TOKENS = frozenset(
    {
        "forward",
        "future",
        "mfe",
        "mae",
        "outcome",
        "target",
        "trade",
        "profit",
        "profitability",
        "profits",
        "pnl",
        "label",
        "returns",
        "trades",
        "targets",
        "continuation",
        "reversal",
        "hit",
    }
)


class FeatureFamily(str, Enum):
    AUCTION = "auction"
    SEQUENCE = "sequence"


class FeatureValueKind(str, Enum):
    FLOAT = "float"
    INTEGER = "integer"
    CATEGORY = "category"


class MissingPolicy(str, Enum):
    NULL = "null"
    ERROR = "error"


class LeakageClass(str, Enum):
    AT_CUTOFF = "AT_CUTOFF"
    TRAILING_ONLY = "TRAILING_ONLY"
    TRAIN_FITTED = "TRAIN_FITTED"
    OUTCOME_OR_FUTURE = "OUTCOME_OR_FUTURE"


@dataclass(frozen=True)
class FeatureDefinition:
    name: str
    definition: str
    family: FeatureFamily
    value_kind: FeatureValueKind
    units: str
    required_prior_observations: int
    missing_policy: MissingPolicy
    version: str
    leakage_class: LeakageClass
    allowed_categories: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or _FEATURE_NAME.fullmatch(self.name) is None:
            raise ValueError("name must be safe lower_snake_case")
        for value, field in ((self.definition, "definition"), (self.units, "units")):
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{field} must be non-empty")
        if not isinstance(self.version, str) or _VERSION.fullmatch(self.version) is None:
            raise ValueError("version must contain only letters, digits, dot, underscore, or hyphen")
        if (
            isinstance(self.required_prior_observations, bool)
            or not isinstance(self.required_prior_observations, int)
            or self.required_prior_observations < 0
        ):
            raise ValueError("required_prior_observations must be a non-negative integer")
        for value, expected, field in (
            (self.family, FeatureFamily, "family"),
            (self.value_kind, FeatureValueKind, "value_kind"),
            (self.missing_policy, MissingPolicy, "missing_policy"),
            (self.leakage_class, LeakageClass, "leakage_class"),
        ):
            if not isinstance(value, expected):
                raise TypeError(f"{field} must be a {expected.__name__}")
        categories = tuple(sorted(self.allowed_categories))
        if len(categories) != len(set(categories)) or any(
            not isinstance(item, str) or not item.strip() for item in categories
        ):
            raise ValueError("allowed_categories must contain unique non-empty strings")
        if self.value_kind is FeatureValueKind.CATEGORY and not categories:
            raise ValueError("allowed_categories are required for category features")
        if self.value_kind is not FeatureValueKind.CATEGORY and categories:
            raise ValueError("allowed_categories apply only to category features")
        object.__setattr__(self, "allowed_categories", categories)

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "definition": self.definition,
            "family": self.family.value,
            "value_kind": self.value_kind.value,
            "units": self.units,
            "required_prior_observations": self.required_prior_observations,
            "missing_policy": self.missing_policy.value,
            "version": self.version,
            "leakage_class": self.leakage_class.value,
            "allowed_categories": list(self.allowed_categories),
        }


class FeatureRegistry:
    """Immutable feature set identified by a manually allocated decimal ID."""

    def __init__(
        self, feature_set_id: str, definitions: Iterable[FeatureDefinition]
    ) -> None:
        if not isinstance(feature_set_id, str) or _FEATURE_SET_ID.fullmatch(feature_set_id) is None:
            raise ValueError("feature_set_id must match FS-######")
        ordered = tuple(sorted(definitions, key=lambda item: item.name))
        if not ordered:
            raise ValueError("a registry requires at least one feature definition")
        if any(not isinstance(item, FeatureDefinition) for item in ordered):
            raise TypeError("definitions must contain only FeatureDefinition values")
        names = tuple(item.name for item in ordered)
        if len(names) != len(set(names)):
            raise ValueError("duplicate feature name in registry")
        self._feature_set_id = feature_set_id
        self._definitions = ordered
        self._names = names
        self.audit_discovery()
        canonical = self.canonical_json()
        self._sha256 = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        self._registry_id = f"FR-{self._sha256[:12].upper()}"

    @property
    def feature_set_id(self) -> str:
        return self._feature_set_id

    @property
    def definitions(self) -> tuple[FeatureDefinition, ...]:
        return self._definitions

    @property
    def names(self) -> tuple[str, ...]:
        return self._names

    @property
    def sha256(self) -> str:
        return self._sha256

    @property
    def registry_id(self) -> str:
        return self._registry_id

    def to_dict(self) -> dict[str, object]:
        return {
            "feature_set_id": self.feature_set_id,
            "definitions": [item.to_dict() for item in self.definitions],
        }

    def canonical_json(self) -> str:
        return json.dumps(
            self.to_dict(),
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )

    def audit_discovery(self) -> None:
        for item in self.definitions:
            tokens = frozenset(item.name.split("_"))
            if tokens & _PROHIBITED_DISCOVERY_TOKENS:
                raise ValueError(f"prohibited discovery feature name: {item.name}")
            if item.leakage_class is LeakageClass.OUTCOME_OR_FUTURE:
                raise ValueError(
                    f"discovery feature {item.name!r} uses OUTCOME_OR_FUTURE leakage class"
                )

    def validate_row(self, row: FeatureRow) -> None:
        if not isinstance(row, FeatureRow):
            raise TypeError("row must be a FeatureRow")
        if row.feature_set_id != self.feature_set_id:
            raise ValueError("row feature_set_id does not match the registry")
        if row.registry_id != self.registry_id:
            raise ValueError("row registry_id does not match the registry")
        if tuple(row.values) != self.names:
            raise ValueError("row feature columns must exactly match the registry")
        for item in self.definitions:
            self._validate_value(item, row.values[item.name])

    @staticmethod
    def _validate_value(item: FeatureDefinition, value: object) -> None:
        if value is None:
            if item.missing_policy is MissingPolicy.ERROR:
                raise ValueError(f"feature {item.name!r} does not allow null")
            return
        if item.value_kind is FeatureValueKind.FLOAT:
            if isinstance(value, bool) or not isinstance(value, float):
                raise TypeError(f"feature {item.name!r} must be a float or null")
            if not math.isfinite(value):
                raise ValueError(f"feature {item.name!r} must be finite or null")
            return
        if item.value_kind is FeatureValueKind.INTEGER:
            if isinstance(value, bool) or not isinstance(value, int):
                raise TypeError(f"feature {item.name!r} must be an integer or null")
            return
        if not isinstance(value, str):
            raise TypeError(f"feature {item.name!r} must be a declared category or null")
        if value not in item.allowed_categories:
            raise ValueError(f"feature {item.name!r} has an undeclared category")
