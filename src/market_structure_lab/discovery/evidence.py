"""Validated frozen evidence and AI interpretation records."""

from __future__ import annotations

import json
import math
import re
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta

from market_structure_lab.discovery.behaviours import FrozenBehaviour
from market_structure_lab.discovery.transitions import ClusterTransitionMatrix
from market_structure_lab.features.registry import validate_discovery_field_name

_RUN_ID = re.compile(r"^DR-[0-9]{6}$")
_BEHAVIOUR_ID = re.compile(r"^B-[A-F0-9]{16}$")
_SHA256 = re.compile(r"^[a-f0-9]{64}$")
_METRIC_NAME = re.compile(r"^[a-z][a-z0-9]*(?:_[a-z0-9]+)*$")
_FORBIDDEN_CLAIM = re.compile(
    r"\b(?:validated edge|profitable|profitability|positive expectancy|"
    r"guaranteed|money printer)\b",
    re.IGNORECASE,
)
_FORBIDDEN_NARRATIVE = re.compile(r"\b(?:institutional|whales?|smart[- ]money)\b", re.IGNORECASE)
_NEUTRAL_NAME_WORDS = frozenset(
    {
        "acceptance",
        "alternating",
        "auction",
        "balance",
        "balanced",
        "behaviour",
        "candle",
        "configuration",
        "contracting",
        "declining",
        "expanding",
        "falling",
        "high",
        "imbalance",
        "imbalanced",
        "location",
        "low",
        "migration",
        "narrow",
        "pattern",
        "persistent",
        "profile",
        "recurring",
        "rejection",
        "return",
        "rising",
        "sequence",
        "state",
        "stationary",
        "structure",
        "transient",
        "value",
        "volatility",
        "volume",
        "wide",
    }
)
_NEUTRAL_NAME_ENDINGS = frozenset(
    {"behaviour", "configuration", "pattern", "sequence", "state", "structure"}
)


@dataclass(frozen=True, slots=True)
class BehaviourEvidencePack:
    """Bounded summaries supplied for interpretation, never raw rows or outcomes."""

    run_id: str
    behaviour: FrozenBehaviour
    nearest_behaviour_ids: tuple[str, ...]
    contrasting_behaviour_ids: tuple[str, ...]
    transition_matrix: ClusterTransitionMatrix

    def __post_init__(self) -> None:
        _require_run_id(self.run_id)
        if not isinstance(self.behaviour, FrozenBehaviour):
            raise TypeError("behaviour must be a FrozenBehaviour")
        if self.behaviour.discovery_run_id != self.run_id:
            raise ValueError("evidence run_id must match the frozen behaviour")
        nearest = _canonical_behaviour_ids(self.nearest_behaviour_ids, "nearest_behaviour_ids")
        contrasting = _canonical_behaviour_ids(
            self.contrasting_behaviour_ids, "contrasting_behaviour_ids"
        )
        if self.behaviour.behaviour_id in set(nearest) | set(contrasting):
            raise ValueError("evidence neighbours cannot contain the source behaviour")
        if set(nearest) & set(contrasting):
            raise ValueError("nearest and contrasting behaviour IDs cannot overlap")
        if not isinstance(self.transition_matrix, ClusterTransitionMatrix):
            raise TypeError("transition_matrix must be a ClusterTransitionMatrix")
        object.__setattr__(self, "nearest_behaviour_ids", nearest)
        object.__setattr__(self, "contrasting_behaviour_ids", contrasting)

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": "behaviour-evidence-pack-v2",
            "run_id": self.run_id,
            "behaviour": asdict(self.behaviour),
            "nearest_behaviour_ids": list(self.nearest_behaviour_ids),
            "contrasting_behaviour_ids": list(self.contrasting_behaviour_ids),
            "transition_matrix": asdict(self.transition_matrix),
        }

    def canonical_json(self) -> str:
        return _canonical_json(self.to_dict()).decode("utf-8")


@dataclass(frozen=True, slots=True)
class AIInterpretation:
    """A neutral, provenance-complete candidate interpretation of frozen evidence."""

    behaviour_id: str
    neutral_name: str
    description: str
    candidate_mechanism_inference: str
    falsifiable_hypothesis: str
    detector_fields: tuple[str, ...]
    proposed_horizon: str
    proposed_metrics: tuple[str, ...]
    spuriousness_reasons: tuple[str, ...]
    provider: str
    model: str
    prompt_sha256: str
    temperature: float
    generated_at: datetime
    response_sha256: str

    def __post_init__(self) -> None:
        _require_behaviour_id(self.behaviour_id)
        _require_neutral_name(self.neutral_name)
        text_fields = (
            (self.neutral_name, "neutral_name"),
            (self.description, "description"),
            (self.candidate_mechanism_inference, "candidate_mechanism_inference"),
            (self.falsifiable_hypothesis, "falsifiable_hypothesis"),
            (self.proposed_horizon, "proposed_horizon"),
            (self.provider, "provider"),
            (self.model, "model"),
        )
        for value, label in text_fields:
            _require_text(value, label)
            if _FORBIDDEN_CLAIM.search(value):
                raise ValueError(f"{label} must not claim validation or profitability")
            if _FORBIDDEN_NARRATIVE.search(value):
                raise ValueError(f"{label} must not contain unsupported participant narratives")
        if not self.candidate_mechanism_inference.startswith("Inference:"):
            raise ValueError("candidate_mechanism_inference must be explicitly inference-labelled")
        detector_fields = _canonical_field_names(self.detector_fields, "detector_fields")
        proposed_metrics = _canonical_metric_names(self.proposed_metrics, "proposed_metrics")
        reasons = _canonical_texts(self.spuriousness_reasons, "spuriousness_reasons")
        _require_sha256(self.prompt_sha256, "prompt_sha256")
        _require_sha256(self.response_sha256, "response_sha256")
        if (
            isinstance(self.temperature, bool)
            or not isinstance(self.temperature, (float, int))
            or not math.isfinite(float(self.temperature))
            or not 0.0 <= float(self.temperature) <= 2.0
        ):
            raise ValueError("temperature must be finite and between zero and two")
        _require_utc(self.generated_at, "generated_at")
        object.__setattr__(self, "detector_fields", detector_fields)
        object.__setattr__(self, "proposed_metrics", proposed_metrics)
        object.__setattr__(self, "spuriousness_reasons", reasons)
        object.__setattr__(self, "temperature", float(self.temperature))

    def to_dict(self) -> dict[str, object]:
        payload = asdict(self)
        payload["generated_at"] = _format_utc(self.generated_at)
        return payload


def _canonical_behaviour_ids(values: tuple[str, ...], label: str) -> tuple[str, ...]:
    if not isinstance(values, tuple):
        raise TypeError(f"{label} must be a tuple")
    for value in values:
        _require_behaviour_id(value)
    if len(set(values)) != len(values):
        raise ValueError(f"{label} must be unique")
    return tuple(sorted(values))


def _canonical_field_names(values: tuple[str, ...], label: str) -> tuple[str, ...]:
    if not isinstance(values, tuple) or not values:
        raise ValueError(f"{label} must be a non-empty tuple")
    for value in values:
        validate_discovery_field_name(value, field=label)
    if len(set(values)) != len(values):
        raise ValueError(f"{label} must be unique")
    return tuple(values)


def _canonical_texts(values: tuple[str, ...], label: str) -> tuple[str, ...]:
    if not isinstance(values, tuple) or not values:
        raise ValueError(f"{label} must be a non-empty tuple")
    for value in values:
        _require_text(value, label)
    if len(set(values)) != len(values):
        raise ValueError(f"{label} must be unique")
    return tuple(values)


def _canonical_metric_names(values: tuple[str, ...], label: str) -> tuple[str, ...]:
    if not isinstance(values, tuple) or not values:
        raise ValueError(f"{label} must be a non-empty tuple")
    if any(not isinstance(value, str) or _METRIC_NAME.fullmatch(value) is None for value in values):
        raise ValueError(f"{label} must contain safe lower_snake_case names")
    if len(set(values)) != len(values):
        raise ValueError(f"{label} must be unique")
    return tuple(values)


def _require_run_id(value: str) -> None:
    if not isinstance(value, str) or _RUN_ID.fullmatch(value) is None:
        raise ValueError("run_id must match DR-######")


def _require_behaviour_id(value: str) -> None:
    if not isinstance(value, str) or _BEHAVIOUR_ID.fullmatch(value) is None:
        raise ValueError("behaviour_id must use the canonical B- identifier")


def _require_sha256(value: str, label: str) -> None:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise ValueError(f"{label} must be a lowercase SHA-256")


def _require_text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be non-empty")
    return value


def _require_neutral_name(value: object) -> str:
    name = _require_text(value, "neutral_name")
    words = name.split()
    lowered = tuple(word.lower() for word in words)
    if (
        not 2 <= len(words) <= 6
        or name != " ".join(word.capitalize() for word in lowered)
        or lowered[-1] not in _NEUTRAL_NAME_ENDINGS
        or any(word not in _NEUTRAL_NAME_WORDS for word in lowered)
    ):
        raise ValueError("neutral_name must use the canonical observable-language grammar")
    return name


def _require_utc(value: datetime, label: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{label} must be UTC-aware")
    if value.utcoffset() != timedelta(0):
        raise ValueError(f"{label} must use UTC")
    return value.astimezone(UTC)


def _format_utc(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
