"""Canonical reader for the frozen Phase 5 validation-programme config."""

from __future__ import annotations

from dataclasses import fields
import json
from pathlib import Path
from typing import cast

from market_structure_lab.core.artifact_io import read_bounded_regular
from market_structure_lab.research.models import (
    VALIDATION_SLOT_ROSTER,
    ValidationProgrammeConfig,
    ValidationWorkBudget,
)

_MAX_CONFIG_BYTES = 16 * 1024 * 1024
_CONFIG_IDENTITY_KEYS = {"config_sha256", "programme_id"}


def _require_string(value: object) -> str:
    if not isinstance(value, str):
        raise TypeError("validation config string field has invalid type")
    return value


def _require_int(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{label} must be a non-negative integer")
    return value


def load_validation_programme_config(path: Path) -> ValidationProgrammeConfig:
    """Load and verify one exact frozen validation-programme config."""

    try:
        decoded = json.loads(read_bounded_regular(Path(path), _MAX_CONFIG_BYTES).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError(f"{Path(path).name} is not valid UTF-8 JSON") from error
    if not isinstance(decoded, dict) or any(not isinstance(key, str) for key in decoded):
        raise TypeError(f"{Path(path).name} must contain a JSON object")
    payload = cast(dict[str, object], decoded)
    expected_config_keys = set(ValidationProgrammeConfig.__dataclass_fields__) - {
        "_roster_sha256",
        "_config_dict",
        "_config_sha256",
        "_programme_id",
    }
    expected_file_keys = (
        expected_config_keys | {"roster_sha256", "work_budget_sha256"} | _CONFIG_IDENTITY_KEYS
    )
    if set(payload) != expected_file_keys:
        raise ValueError("validation config has unexpected or missing fields")
    if payload["roster"] != [slot.to_dict() for slot in VALIDATION_SLOT_ROSTER]:
        raise ValueError("validation config must contain the exact frozen 1,104-slot roster")
    raw_budget = payload["work_budget"]
    if not isinstance(raw_budget, dict):
        raise TypeError("work_budget must be an object")
    budget_fields = {item.name for item in fields(ValidationWorkBudget)}
    if set(raw_budget) != budget_fields:
        raise ValueError("work_budget has unexpected or missing fields")
    budget = ValidationWorkBudget(
        **{name: _require_int(raw_budget[name], name) for name in sorted(budget_fields)}
    )
    raw_families = payload["families"]
    if not isinstance(raw_families, list) or any(
        not isinstance(item, str) for item in raw_families
    ):
        raise TypeError("families must be a list of strings")
    config = ValidationProgrammeConfig(
        task14_closeout_commit=_require_string(payload["task14_closeout_commit"]),
        task14_evidence_commit=_require_string(payload["task14_evidence_commit"]),
        task15_plan_commit=_require_string(payload["task15_plan_commit"]),
        task15_evidence_commit=_require_string(payload["task15_evidence_commit"]),
        implementation_plan_checkpoint=_require_string(payload["implementation_plan_checkpoint"]),
        implementation_plan_evidence_commit=_require_string(
            payload["implementation_plan_evidence_commit"]
        ),
        implementation_plan_document_sha256=_require_string(
            payload["implementation_plan_document_sha256"]
        ),
        code_commit=_require_string(payload["code_commit"]),
        lockfile_sha256=_require_string(payload["lockfile_sha256"]),
        dataset_sha256=_require_string(payload["dataset_sha256"]),
        cost_policy_sha256=_require_string(payload["cost_policy_sha256"]),
        control_policy_sha256=_require_string(payload["control_policy_sha256"]),
        split_sha256=_require_string(payload["split_sha256"]),
        profile_config_sha256=_require_string(payload["profile_config_sha256"]),
        source_price_precision_sha256=_require_string(payload["source_price_precision_sha256"]),
        families=tuple(cast(list[str], raw_families)),
        roster=VALIDATION_SLOT_ROSTER,
        work_budget=budget,
    )
    if payload["roster_sha256"] != config.roster_sha256:
        raise ValueError("roster_sha256 does not match the frozen roster")
    if payload["work_budget_sha256"] != config.work_budget.sha256:
        raise ValueError("work_budget_sha256 does not match the frozen budget")
    if payload["config_sha256"] != config.sha256:
        raise ValueError("config_sha256 does not match the canonical config")
    if payload["programme_id"] != config.programme_id:
        raise ValueError("programme_id does not match the canonical config")
    if {
        key: value for key, value in payload.items() if key not in _CONFIG_IDENTITY_KEYS
    } != config.to_dict():
        raise ValueError("validation config is not canonical")
    return config


__all__ = ["load_validation_programme_config"]
