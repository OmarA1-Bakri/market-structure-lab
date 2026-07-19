"""Versioned feature metadata and fail-closed discovery leakage audit."""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass, replace
from enum import Enum
from typing import Iterable

from market_structure_lab.features.models import FeatureRow

_FEATURE_NAME = re.compile(r"^[a-z][a-z0-9]*(?:_[a-z0-9]+)*$")
_VERSION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_FEATURE_SET_ID = re.compile(r"^FS-[0-9]{6}$")
_SOURCE_FIELD = re.compile(r"^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)*$")
_TRAILING_WINDOW = re.compile(r"^trailing_([1-9][0-9]*)_observations$")
BUILTIN_FEATURE_BUILDER_ID = "market_structure_lab.features.builder.FeatureBuilder"
BUILTIN_FEATURE_BUILDER_VERSION = "causal-feature-builder-v2"
MAX_FEATURE_DEFINITIONS = 10_000
MAX_FEATURE_NAME_LENGTH = 127
MAX_FEATURE_DEFINITION_LENGTH = 4096
MAX_FEATURE_UNITS_LENGTH = 127
MAX_FEATURE_SOURCE_FIELDS = 64
MAX_FEATURE_SOURCE_FIELD_LENGTH = 255
MAX_FEATURE_CATEGORIES = 256
MAX_FEATURE_CATEGORY_LENGTH = 127
MAX_FEATURE_IDENTITY_LENGTH = 255
MAX_FEATURE_VERSION_LENGTH = 127
MAX_TRAILING_WINDOW_LENGTH = 64
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


def validate_discovery_field_name(name: str, *, field: str) -> None:
    """Require a lower-snake-case name with no future or outcome token."""

    if not isinstance(name, str) or _FEATURE_NAME.fullmatch(name) is None:
        raise ValueError(f"{field} must be safe lower_snake_case")
    if len(name) > MAX_FEATURE_NAME_LENGTH:
        raise ValueError(f"{field} exceeds the length limit")
    if frozenset(name.split("_")) & _PROHIBITED_DISCOVERY_TOKENS:
        raise ValueError(f"prohibited outcome or future {field}: {name}")


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


class ObservableCutoffRule(str, Enum):
    AT_INFORMATION_CUTOFF = "at_information_cutoff"
    TRAILING_THROUGH_INFORMATION_CUTOFF = "trailing_through_information_cutoff"
    CENTERED_WINDOW = "centered_window"
    AFTER_DECLARED_CUTOFF = "after_declared_cutoff"
    FUTURE_DEPENDENT_LABEL = "future_dependent_label"


class NormalizationRequirement(str, Enum):
    NOT_REQUIRED = "not_required"
    TRAINING_PARTITION_FITTED = "training_partition_fitted"
    FULL_PERIOD = "full_period"
    GLOBAL_MIN_MAX = "global_min_max"


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
    source_fields: tuple[str, ...]
    trailing_window: str
    observable_cutoff_rule: ObservableCutoffRule
    normalization_requirement: NormalizationRequirement
    future_outcome_prohibited: bool
    builder_id: str
    builder_version: str
    allowed_categories: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or _FEATURE_NAME.fullmatch(self.name) is None:
            raise ValueError("name must be safe lower_snake_case")
        if len(self.name) > MAX_FEATURE_NAME_LENGTH:
            raise ValueError("name exceeds the length limit")
        for value, field in ((self.definition, "definition"), (self.units, "units")):
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{field} must be non-empty")
        if len(self.definition) > MAX_FEATURE_DEFINITION_LENGTH:
            raise ValueError("definition exceeds the length limit")
        if len(self.units) > MAX_FEATURE_UNITS_LENGTH:
            raise ValueError("units exceeds the length limit")
        if (
            not isinstance(self.version, str)
            or len(self.version) > MAX_FEATURE_VERSION_LENGTH
            or _VERSION.fullmatch(self.version) is None
        ):
            raise ValueError(
                "version must contain only letters, digits, dot, underscore, or hyphen"
            )
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
            (self.observable_cutoff_rule, ObservableCutoffRule, "observable_cutoff_rule"),
            (
                self.normalization_requirement,
                NormalizationRequirement,
                "normalization_requirement",
            ),
        ):
            if not isinstance(value, expected):
                raise TypeError(f"{field} must be a {expected.__name__}")
        if not isinstance(self.allowed_categories, tuple):
            raise TypeError("allowed_categories must be a tuple")
        if len(self.allowed_categories) > MAX_FEATURE_CATEGORIES:
            raise ValueError("allowed_categories exceeds the bounded count")
        categories = tuple(sorted(self.allowed_categories))
        if len(categories) != len(set(categories)) or any(
            not isinstance(item, str) or not item.strip() or len(item) > MAX_FEATURE_CATEGORY_LENGTH
            for item in categories
        ):
            raise ValueError("allowed_categories must contain unique non-empty strings")
        if self.value_kind is FeatureValueKind.CATEGORY and not categories:
            raise ValueError("allowed_categories are required for category features")
        if self.value_kind is not FeatureValueKind.CATEGORY and categories:
            raise ValueError("allowed_categories apply only to category features")
        if (
            not isinstance(self.source_fields, tuple)
            or not self.source_fields
            or len(self.source_fields) > MAX_FEATURE_SOURCE_FIELDS
            or self.source_fields != tuple(sorted(set(self.source_fields)))
            or any(
                not isinstance(item, str)
                or len(item) > MAX_FEATURE_SOURCE_FIELD_LENGTH
                or _SOURCE_FIELD.fullmatch(item) is None
                for item in self.source_fields
            )
        ):
            raise ValueError("source_fields must be unique sorted observable field paths")
        if not isinstance(self.trailing_window, str):
            raise TypeError("trailing_window must be a string")
        if len(self.trailing_window) > MAX_TRAILING_WINDOW_LENGTH:
            raise ValueError("trailing_window exceeds the length limit")
        match = _TRAILING_WINDOW.fullmatch(self.trailing_window)
        if self.trailing_window == "current_observation":
            window_observations = 1
        elif self.trailing_window == "since_continuity_boundary":
            window_observations = None
        elif match is not None:
            window_observations = int(match.group(1))
        else:
            raise ValueError("trailing_window must declare a causal observation window")
        if (
            window_observations is not None
            and window_observations != self.required_prior_observations + 1
        ):
            raise ValueError("trailing_window must match required warm-up observations")
        if self.trailing_window == "current_observation" and (
            self.observable_cutoff_rule is not ObservableCutoffRule.AT_INFORMATION_CUTOFF
        ):
            raise ValueError("current observation features require the at-cutoff rule")
        if not isinstance(self.future_outcome_prohibited, bool):
            raise TypeError("future_outcome_prohibited must be a boolean")
        for value, field in (
            (self.builder_id, "builder_id"),
            (self.builder_version, "builder_version"),
        ):
            if (
                not isinstance(value, str)
                or len(value) > MAX_FEATURE_IDENTITY_LENGTH
                or _VERSION.fullmatch(value) is None
            ):
                raise ValueError(f"{field} must be a version-safe registered identity")
        object.__setattr__(self, "allowed_categories", categories)

    @property
    def dependency_contract_sha256(self) -> str:
        return discovery_dependency_contract_sha256(
            source_fields=self.source_fields,
            trailing_window=self.trailing_window,
            observable_cutoff_rule=self.observable_cutoff_rule.value,
            warm_up_observations=self.required_prior_observations,
            normalization_requirement=self.normalization_requirement.value,
            future_outcome_prohibited=self.future_outcome_prohibited,
            builder_id=self.builder_id,
            builder_version=self.builder_version,
        )

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
            "source_fields": list(self.source_fields),
            "trailing_window": self.trailing_window,
            "observable_cutoff_rule": self.observable_cutoff_rule.value,
            "normalization_requirement": self.normalization_requirement.value,
            "future_outcome_prohibited": self.future_outcome_prohibited,
            "builder_id": self.builder_id,
            "builder_version": self.builder_version,
            "allowed_categories": list(self.allowed_categories),
        }


@dataclass(frozen=True, slots=True)
class FeatureRegistrySnapshot:
    """Detached immutable authoritative registry contract for one operation."""

    feature_set_id: str
    definitions: tuple[FeatureDefinition, ...]
    names: tuple[str, ...]
    canonical_json: str
    sha256: str
    registry_id: str
    dependency_contract_sha256: str

    @classmethod
    def create(
        cls,
        feature_set_id: str,
        definitions: Iterable[FeatureDefinition],
    ) -> FeatureRegistrySnapshot:
        source = tuple(definitions)
        if not source:
            raise ValueError("a registry snapshot requires at least one feature definition")
        if len(source) > MAX_FEATURE_DEFINITIONS:
            raise ValueError("registry snapshot definitions exceed the bounded count")
        if any(not isinstance(item, FeatureDefinition) for item in source):
            raise TypeError("snapshot definitions must contain only FeatureDefinition values")
        _audit_discovery_definitions(source)
        detached = tuple(sorted((replace(item) for item in source), key=lambda item: item.name))
        names = tuple(item.name for item in detached)
        if len(names) != len(set(names)):
            raise ValueError("duplicate feature name in registry snapshot")
        _audit_discovery_definitions(detached)
        canonical = _registry_canonical_json(feature_set_id, detached)
        digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        return cls(
            feature_set_id=feature_set_id,
            definitions=detached,
            names=names,
            canonical_json=canonical,
            sha256=digest,
            registry_id=f"FR-{digest[:12].upper()}",
            dependency_contract_sha256=_aggregate_dependency_contract_sha256(detached),
        )

    def audit_discovery(self) -> None:
        _audit_discovery_definitions(self.definitions)

    def validate_row(self, row: FeatureRow) -> None:
        _validate_registry_row(self, row)


class FeatureRegistry:
    """Immutable feature set identified by a manually allocated decimal ID."""

    def __init__(self, feature_set_id: str, definitions: Iterable[FeatureDefinition]) -> None:
        if not isinstance(feature_set_id, str) or _FEATURE_SET_ID.fullmatch(feature_set_id) is None:
            raise ValueError("feature_set_id must match FS-######")
        bounded: list[FeatureDefinition] = []
        for definition in definitions:
            if len(bounded) >= MAX_FEATURE_DEFINITIONS:
                raise ValueError(
                    f"a registry may contain at most {MAX_FEATURE_DEFINITIONS} definitions"
                )
            bounded.append(definition)
        ordered = tuple(sorted(bounded, key=lambda item: item.name))
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
        canonical = _registry_canonical_json(feature_set_id, ordered)
        self._sha256 = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        self._registry_id = f"FR-{self._sha256[:12].upper()}"
        self._dependency_contract_sha256 = _aggregate_dependency_contract_sha256(ordered)

    def snapshot(self) -> FeatureRegistrySnapshot:
        """Freeze and validate one detached authoritative registry contract."""

        snapshot = FeatureRegistrySnapshot.create(self._feature_set_id, self._definitions)
        expected = (
            self._feature_set_id,
            self._names,
            self.canonical_json(),
            self._sha256,
            self._registry_id,
            self._dependency_contract_sha256,
        )
        actual = (
            snapshot.feature_set_id,
            snapshot.names,
            snapshot.canonical_json,
            snapshot.sha256,
            snapshot.registry_id,
            snapshot.dependency_contract_sha256,
        )
        if actual != expected:
            raise ValueError("live feature registry mutated after identity construction")
        return snapshot

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

    @property
    def dependency_contract_sha256(self) -> str:
        return self._dependency_contract_sha256

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
        _audit_discovery_definitions(self.definitions)

    def validate_row(self, row: FeatureRow) -> None:
        _validate_registry_row(self, row)

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


def _registry_canonical_json(
    feature_set_id: str,
    definitions: tuple[FeatureDefinition, ...],
) -> str:
    if not isinstance(feature_set_id, str) or _FEATURE_SET_ID.fullmatch(feature_set_id) is None:
        raise ValueError("feature_set_id must match FS-######")
    return json.dumps(
        {
            "feature_set_id": feature_set_id,
            "definitions": [item.to_dict() for item in definitions],
        },
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _aggregate_dependency_contract_sha256(
    definitions: tuple[FeatureDefinition, ...],
) -> str:
    return hashlib.sha256(
        json.dumps(
            {item.name: item.dependency_contract_sha256 for item in definitions},
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()


def validate_discovery_dependency_contract(
    *,
    feature_name: str,
    source_fields: tuple[str, ...],
    trailing_window: str,
    observable_cutoff_rule: str,
    warm_up_observations: int,
    normalization_requirement: str,
    future_outcome_prohibited: bool,
    builder_id: str,
    builder_version: str,
) -> None:
    """Validate a receipt's dependency fields as a causal discovery contract."""

    validate_discovery_field_name(feature_name, field="discovery feature name")
    if (
        not isinstance(source_fields, tuple)
        or not source_fields
        or source_fields != tuple(sorted(set(source_fields)))
        or any(
            not isinstance(source, str)
            or len(source) > MAX_FEATURE_SOURCE_FIELD_LENGTH
            or _SOURCE_FIELD.fullmatch(source) is None
            for source in source_fields
        )
    ):
        raise ValueError("source_fields must be unique sorted observable field paths")
    source_tokens = {
        token
        for source in source_fields
        for component in source.split(".")
        for token in component.split("_")
    }
    if source_tokens & _PROHIBITED_DISCOVERY_TOKENS:
        raise ValueError(f"discovery feature {feature_name!r} uses a future or outcome source")
    if isinstance(warm_up_observations, bool) or not isinstance(warm_up_observations, int):
        raise TypeError("warm_up_observations must be an integer")
    if warm_up_observations < 0:
        raise ValueError("warm_up_observations must be non-negative")
    if not isinstance(trailing_window, str):
        raise TypeError("trailing_window must be a string")
    match = _TRAILING_WINDOW.fullmatch(trailing_window)
    if trailing_window == "current_observation":
        window_observations: int | None = 1
    elif trailing_window == "since_continuity_boundary":
        window_observations = None
    elif match is not None:
        window_observations = int(match.group(1))
    else:
        raise ValueError("trailing_window must declare a causal observation window")
    if window_observations is not None and window_observations != warm_up_observations + 1:
        raise ValueError("trailing_window must match required warm-up observations")
    try:
        cutoff = ObservableCutoffRule(observable_cutoff_rule)
    except (TypeError, ValueError) as error:
        raise ValueError("observable_cutoff_rule is not recognized") from error
    try:
        normalization = NormalizationRequirement(normalization_requirement)
    except (TypeError, ValueError) as error:
        raise ValueError("normalization_requirement is not recognized") from error
    if cutoff is ObservableCutoffRule.CENTERED_WINDOW:
        raise ValueError(f"discovery feature {feature_name!r} uses a centered window")
    if cutoff is ObservableCutoffRule.FUTURE_DEPENDENT_LABEL:
        raise ValueError(f"discovery feature {feature_name!r} uses a future-dependent label")
    if cutoff is ObservableCutoffRule.AFTER_DECLARED_CUTOFF:
        raise ValueError(f"discovery feature {feature_name!r} is observable after declared cutoff")
    if normalization is NormalizationRequirement.FULL_PERIOD:
        raise ValueError(f"discovery feature {feature_name!r} uses full-period normalization")
    if normalization is NormalizationRequirement.GLOBAL_MIN_MAX:
        raise ValueError(f"discovery feature {feature_name!r} uses global min/max normalization")
    if trailing_window == "current_observation" and (
        cutoff is not ObservableCutoffRule.AT_INFORMATION_CUTOFF
    ):
        raise ValueError("current observation features require the at-cutoff rule")
    if future_outcome_prohibited is not True:
        raise ValueError(f"discovery feature {feature_name!r} lacks future/outcome prohibition")
    if (
        builder_id != BUILTIN_FEATURE_BUILDER_ID
        or builder_version != BUILTIN_FEATURE_BUILDER_VERSION
    ):
        raise ValueError(f"discovery feature {feature_name!r} lacks a registered builder")


def discovery_dependency_contract_sha256(
    *,
    source_fields: tuple[str, ...],
    trailing_window: str,
    observable_cutoff_rule: str,
    warm_up_observations: int,
    normalization_requirement: str,
    future_outcome_prohibited: bool,
    builder_id: str,
    builder_version: str,
) -> str:
    """Hash the exact dependency fields embedded in a registry or receipt."""

    return hashlib.sha256(
        json.dumps(
            {
                "source_fields": list(source_fields),
                "trailing_window": trailing_window,
                "observable_cutoff_rule": observable_cutoff_rule,
                "warm_up_observations": warm_up_observations,
                "normalization_requirement": normalization_requirement,
                "future_outcome_prohibited": future_outcome_prohibited,
                "builder_id": builder_id,
                "builder_version": builder_version,
            },
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()


def _audit_discovery_definitions(definitions: tuple[FeatureDefinition, ...]) -> None:
    for item in definitions:
        if item.leakage_class is LeakageClass.OUTCOME_OR_FUTURE:
            raise ValueError(
                f"discovery feature {item.name!r} uses OUTCOME_OR_FUTURE leakage class"
            )
        validate_discovery_dependency_contract(
            feature_name=item.name,
            source_fields=item.source_fields,
            trailing_window=item.trailing_window,
            observable_cutoff_rule=item.observable_cutoff_rule.value,
            warm_up_observations=item.required_prior_observations,
            normalization_requirement=item.normalization_requirement.value,
            future_outcome_prohibited=item.future_outcome_prohibited,
            builder_id=item.builder_id,
            builder_version=item.builder_version,
        )


def _validate_registry_row(
    registry: FeatureRegistry | FeatureRegistrySnapshot,
    row: FeatureRow,
) -> None:
    if not isinstance(row, FeatureRow):
        raise TypeError("row must be a FeatureRow")
    if row.feature_set_id != registry.feature_set_id:
        raise ValueError("row feature_set_id does not match the registry")
    if row.registry_id != registry.registry_id:
        raise ValueError("row registry_id does not match the registry")
    if tuple(row.values) != registry.names:
        raise ValueError("row feature columns must exactly match the registry")
    for item in registry.definitions:
        FeatureRegistry._validate_value(item, row.values[item.name])
