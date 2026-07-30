"""Run one preregistered Phase 5 development-only validation programme."""

from __future__ import annotations

import argparse
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, fields
from datetime import UTC, datetime
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys
import tempfile
from types import MappingProxyType
from typing import cast

from market_structure_lab.core.artifact_io import read_bounded_regular
from market_structure_lab.core.identity import hash_json
from market_structure_lab.data.reconciliation.receipts import (
    ReconciliationPromotionReceipt,
    read_reconciliation_promotion_receipt,
)
from market_structure_lab.data.reconciliation.repository import VerifiedCoverageInterval
from market_structure_lab.research.models import (
    VALIDATION_SLOT_ROSTER,
    ValidationProgrammeConfig,
    ValidationWorkBudget,
)
from market_structure_lab.research.validation import publish_validation_preflight_failure

_MAX_CONFIG_BYTES = 16 * 1024 * 1024
_MAX_PREFLIGHT_BYTES = 4 * 1024 * 1024
_SOURCE_SCHEMA = "phase5-validation-source-preflight-v1"
_SOURCE_DOMAIN = "phase5-validation-source-preflight"
_BINDINGS_SCHEMA = "phase5-validation-bindings-v1"
_BINDINGS_DOMAIN = "phase5-validation-bindings"
_PROMOTION_RECEIPT_PATH = PurePosixPath(
    "data/exports/reconciliation/promotions/run_id=RR-000008/receipt.json"
)
_PROMOTION_RECEIPT_SHA256 = "cb399ac01d0beeea4666da071d9186e9a144f3f7fe5e4d1bf760696c631e8529"
_COMPATIBILITY_MANIFEST_PATH = PurePosixPath(
    "data/exports/manifests/recovery-callscore-20260714-validated.json"
)
_COMPATIBILITY_MANIFEST_SHA256 = "482f3a09a4e24a62b0ad92f5bb90028eead67fe961eb1d3320dd38d13fd105d2"
_DAY_MS = 86_400_000
_SOURCE_KEYS = {
    "schema_version",
    "component",
    "status",
    "final_holdout_access_count",
    "eligible_symbols",
    "source_conflict_symbols",
    "mapping_incompatible_symbols",
    "common_complete_days",
    "common_complete_start",
    "common_complete_end",
    "source_publication_path",
    "source_publication_sha256",
    "compatibility_manifest_path",
    "compatibility_manifest_sha256",
    "bindings_sha256",
    "missing_prerequisites",
    "manifest_sha256",
}
_BINDING_DOMAINS = {
    "cost_policy_sha256": ("phase5-validation-cost-policy-declaration", "cost_policy"),
    "control_policy_sha256": (
        "phase5-validation-control-policy-declaration",
        "control_policy",
    ),
    "split_sha256": ("phase5-validation-split-declaration", "split_policy"),
    "profile_config_sha256": (
        "phase5-validation-profile-config-declaration",
        "profile_config",
    ),
    "source_price_precision_sha256": (
        "phase5-validation-source-price-precision-declaration",
        "source_price_precision",
    ),
}
_BINDING_KEYS = {
    "schema_version",
    "component",
    "final_holdout_access_count",
    "source_universe",
    "aggregate_config",
    "cost_policy",
    "control_policy",
    "split_policy",
    "profile_config",
    "source_price_precision",
    "binding_hashes",
    "bindings_sha256",
}
_CONFIG_IDENTITY_KEYS = {"config_sha256", "programme_id"}
_CONTROLLED_UNTRACKED = ("src", "tests", "docs", "configs", "pyproject.toml", "uv.lock")
_SOURCE_PATHS = ("src", "pyproject.toml", "uv.lock")
_IMPLEMENTATION_PLAN_PATH = "docs/superpowers/plans/2026-07-22-phase5-bounded-implementation.md"
_SQL_TOKEN = re.compile(r"\b(?:select|insert|update|delete|drop|alter|create)\b", re.I)
_CREDENTIAL_URL = re.compile(r"([a-z][a-z0-9+.-]*://)[^@\s]+@", re.I)
_SECRET_ASSIGNMENT = re.compile(r"\b(password|passwd|token|secret|api[_-]?key)=([^\s]+)", re.I)


@dataclass(frozen=True, slots=True)
class ValidationSourcePreflight:
    """Frozen metadata proving why a real primitive source bundle is unavailable."""

    eligible_symbols: tuple[str, ...]
    source_conflict_symbols: tuple[str, ...]
    mapping_incompatible_symbols: tuple[str, ...]
    common_complete_days: int
    common_complete_start: datetime
    common_complete_end: datetime
    source_publication_path: PurePosixPath
    source_publication_sha256: str
    compatibility_manifest_path: PurePosixPath
    compatibility_manifest_sha256: str
    bindings_sha256: str
    missing_prerequisites: tuple[str, ...]
    manifest_sha256: str
    component: str = "development"
    status: str = "missing_prerequisites"
    final_holdout_access_count: int = 0
    schema_version: str = _SOURCE_SCHEMA


@dataclass(frozen=True, slots=True)
class ValidationBindings:
    """Verified unavailable-state and policy preimages bound by the VP config."""

    binding_hashes: Mapping[str, str]
    source_universe: Mapping[str, object]
    bindings_sha256: str


def load_programme_config(path: Path) -> ValidationProgrammeConfig:
    """Load and verify one exact frozen validation-programme config."""

    payload = _load_json_object(path, maximum=_MAX_CONFIG_BYTES)
    expected_config_keys = set(ValidationProgrammeConfig.__dataclass_fields__) - {
        "_roster_sha256",
        "_config_dict",
        "_config_sha256",
        "_programme_id",
    }
    expected_file_keys = (
        expected_config_keys
        | {
            "roster_sha256",
            "work_budget_sha256",
        }
        | _CONFIG_IDENTITY_KEYS
    )
    if set(payload) != expected_file_keys:
        raise ValueError("validation config has unexpected or missing fields")
    expected_roster = [slot.to_dict() for slot in VALIDATION_SLOT_ROSTER]
    if payload["roster"] != expected_roster:
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


def load_source_preflight(path: Path) -> ValidationSourcePreflight:
    """Load fail-closed development-only source metadata without opening outcome rows."""

    payload = _load_json_object(path, maximum=_MAX_PREFLIGHT_BYTES)
    if set(payload) != _SOURCE_KEYS:
        raise ValueError("source preflight has unexpected or missing fields")
    if payload["schema_version"] != _SOURCE_SCHEMA:
        raise ValueError("source preflight schema_version is unsupported")
    if payload["component"] != "development":
        raise ValueError("source preflight must remain development-only")
    if payload["status"] != "missing_prerequisites":
        raise ValueError("source preflight status must be missing_prerequisites")
    if payload["final_holdout_access_count"] != 0:
        raise ValueError("source preflight final_holdout_access_count must be zero")
    eligible = _require_sorted_strings(payload["eligible_symbols"], "eligible_symbols")
    conflicts = _require_sorted_strings(
        payload["source_conflict_symbols"], "source_conflict_symbols"
    )
    incompatible = _require_sorted_strings(
        payload["mapping_incompatible_symbols"], "mapping_incompatible_symbols"
    )
    forbidden = {"BTCUSDT", "ETHUSDT"}
    contaminated = forbidden.intersection(eligible)
    if contaminated:
        raise ValueError(f"eligible_symbols contains forbidden symbol {sorted(contaminated)[0]}")
    if set(eligible).intersection(conflicts) or set(eligible).intersection(incompatible):
        raise ValueError("eligible_symbols overlap conflict or incompatible symbols")
    if len(eligible) < 5:
        raise ValueError("source preflight requires at least five eligible symbols")
    common_days = _require_int(payload["common_complete_days"], "common_complete_days")
    if common_days < 730:
        raise ValueError("source preflight requires at least 730 common complete days")
    common_start = _require_utc(payload["common_complete_start"], "common_complete_start")
    common_end = _require_utc(payload["common_complete_end"], "common_complete_end")
    if common_end <= common_start:
        raise ValueError("source preflight common complete interval must be non-empty")
    if (common_end - common_start).days != common_days:
        raise ValueError("common_complete_days does not match the frozen interval")
    missing = _require_sorted_strings(payload["missing_prerequisites"], "missing_prerequisites")
    if not missing:
        raise ValueError("missing_prerequisites must not be empty")
    relative_path = _require_relative_path(payload["source_publication_path"])
    source_sha256 = _require_sha256(
        payload["source_publication_sha256"], "source_publication_sha256"
    )
    compatibility_path = _require_relative_path(payload["compatibility_manifest_path"])
    compatibility_sha256 = _require_sha256(
        payload["compatibility_manifest_sha256"], "compatibility_manifest_sha256"
    )
    bindings_sha256 = _require_sha256(payload["bindings_sha256"], "bindings_sha256")
    if relative_path != _PROMOTION_RECEIPT_PATH or source_sha256 != _PROMOTION_RECEIPT_SHA256:
        raise ValueError("source preflight does not bind the authoritative RR-000008 receipt")
    if (
        compatibility_path != _COMPATIBILITY_MANIFEST_PATH
        or compatibility_sha256 != _COMPATIBILITY_MANIFEST_SHA256
    ):
        raise ValueError("source preflight does not bind the authoritative compatibility manifest")
    recorded_sha256 = _require_sha256(payload["manifest_sha256"], "manifest_sha256")
    expected_sha256 = hash_json(
        _SOURCE_DOMAIN,
        {key: value for key, value in payload.items() if key != "manifest_sha256"},
    )
    if recorded_sha256 != expected_sha256:
        raise ValueError("manifest_sha256 does not match the canonical source preflight")
    return ValidationSourcePreflight(
        eligible_symbols=eligible,
        source_conflict_symbols=conflicts,
        mapping_incompatible_symbols=incompatible,
        common_complete_days=common_days,
        common_complete_start=common_start,
        common_complete_end=common_end,
        source_publication_path=relative_path,
        source_publication_sha256=source_sha256,
        compatibility_manifest_path=compatibility_path,
        compatibility_manifest_sha256=compatibility_sha256,
        bindings_sha256=bindings_sha256,
        missing_prerequisites=missing,
        manifest_sha256=recorded_sha256,
    )


def load_validation_bindings(path: Path) -> ValidationBindings:
    """Load and recompute every unavailable-state/policy binding preimage."""

    payload = _load_json_object(path, maximum=_MAX_PREFLIGHT_BYTES)
    if set(payload) != _BINDING_KEYS:
        raise ValueError("validation bindings have unexpected or missing fields")
    if payload["schema_version"] != _BINDINGS_SCHEMA:
        raise ValueError("validation bindings schema_version is unsupported")
    if payload["component"] != "development" or payload["final_holdout_access_count"] != 0:
        raise ValueError("validation bindings must remain development-only with zero final access")
    raw_hashes = payload["binding_hashes"]
    if not isinstance(raw_hashes, dict) or set(raw_hashes) != set(_BINDING_DOMAINS):
        raise ValueError("validation binding hash set is invalid")
    expected_hashes = {
        field: hash_json(domain, payload[key]) for field, (domain, key) in _BINDING_DOMAINS.items()
    }
    if raw_hashes != expected_hashes:
        raise ValueError("validation binding hash does not match its canonical preimage")
    recorded = _require_sha256(payload["bindings_sha256"], "bindings_sha256")
    expected = hash_json(
        _BINDINGS_DOMAIN,
        {key: value for key, value in payload.items() if key != "bindings_sha256"},
    )
    if recorded != expected:
        raise ValueError("bindings_sha256 does not match the canonical binding bundle")
    return ValidationBindings(
        binding_hashes=MappingProxyType(dict(expected_hashes)),
        source_universe=MappingProxyType(
            dict(_require_mapping(payload["source_universe"], "source_universe"))
        ),
        bindings_sha256=recorded,
    )


def build_parser() -> argparse.ArgumentParser:
    """Build the explicit-path Task 10 CLI parser."""

    parser = argparse.ArgumentParser(
        description="Publish one bounded Phase 5 development-only validation result"
    )
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--source-preflight", type=Path, required=True)
    parser.add_argument("--bindings", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--repo-root", type=Path, required=True)
    return parser


def verify_repository_state(repo_root: Path, config: ValidationProgrammeConfig) -> str:
    """Require clean code and live remote proof for every frozen source identity."""

    root = Path(repo_root)
    tracked_status = _git_text(root, "status", "--porcelain=v1", "--untracked-files=no")
    if tracked_status:
        raise ValueError("repository has dirty tracked changes")
    controlled_untracked = _git_text(
        root,
        "ls-files",
        "--others",
        "--exclude-standard",
        "--",
        *_CONTROLLED_UNTRACKED,
    )
    if controlled_untracked:
        raise ValueError("repository has uncommitted controlled files")

    head = _git_text(root, "rev-parse", "HEAD")
    branch = _git_text(root, "symbolic-ref", "--short", "HEAD")
    remote = _git_text(root, "config", "--get", f"branch.{branch}.remote")
    merge_ref = _git_text(root, "config", "--get", f"branch.{branch}.merge")
    if not remote or not merge_ref.startswith("refs/heads/"):
        raise RuntimeError("repository branch has no verified upstream")
    remote_lines = _git_text(root, "ls-remote", "--heads", remote, merge_ref).splitlines()
    if len(remote_lines) != 1:
        raise RuntimeError("remote branch identity could not be verified")
    remote_fields = remote_lines[0].split()
    if len(remote_fields) != 2 or remote_fields[1] != merge_ref:
        raise RuntimeError("remote branch response is invalid")
    remote_head = remote_fields[0]
    if head != remote_head:
        raise ValueError("local HEAD does not equal the live remote branch")

    lineage = (
        config.task14_closeout_commit,
        config.task14_evidence_commit,
        config.task15_plan_commit,
        config.task15_evidence_commit,
        config.implementation_plan_checkpoint,
        config.implementation_plan_evidence_commit,
        config.code_commit,
    )
    for commit in lineage:
        if not _git_check(root, "merge-base", "--is-ancestor", commit, remote_head):
            raise ValueError("required Phase 5 lineage is not remotely reachable")
    if not _git_check(
        root,
        "diff",
        "--quiet",
        f"{config.code_commit}..{head}",
        "--",
        *_SOURCE_PATHS,
    ):
        raise ValueError("implementation source changed after the frozen code checkpoint")

    plan_bytes = _git_bytes(
        root,
        "show",
        f"{config.implementation_plan_checkpoint}:{_IMPLEMENTATION_PLAN_PATH}",
    )
    if hashlib.sha256(plan_bytes).hexdigest() != config.implementation_plan_document_sha256:
        raise ValueError("implementation plan checkpoint document hash does not match")
    lock_bytes = read_bounded_regular(root / "uv.lock", 64 * 1024 * 1024)
    if hashlib.sha256(lock_bytes).hexdigest() != config.lockfile_sha256:
        raise ValueError("lockfile does not match the frozen validation config")
    return remote_head


def verify_preregistration_files(
    repo_root: Path,
    remote_head: str,
    paths: tuple[Path | PurePosixPath, ...],
) -> None:
    """Prove each explicit preregistration input is tracked and equals live remote bytes."""

    root = Path(repo_root)
    for raw_path in paths:
        relative = _repository_relative_path(raw_path)
        relative_text = relative.as_posix()
        if not _git_check(root, "ls-files", "--error-unmatch", "--", relative_text):
            raise ValueError("preregistration input is not tracked")
        local = read_bounded_regular(root.joinpath(*relative.parts), _MAX_CONFIG_BYTES)
        remote = _git_bytes(
            root,
            "show",
            f"{remote_head}:{relative_text}",
            maximum=_MAX_CONFIG_BYTES,
        )
        if local != remote:
            raise ValueError("preregistration input does not match the live remote bytes")


def verify_source_publications(repo_root: Path, source: ValidationSourcePreflight) -> None:
    """Derive the eligible universe and common interval from verified source publications."""

    receipt = _read_verified_promotion_receipt(repo_root, source)
    compatibility = _read_compatibility_states(repo_root, source)
    conflicts = tuple(
        sorted(symbol for symbol, state in compatibility.items() if state == "source_conflict")
    )
    if source.source_conflict_symbols != conflicts:
        raise ValueError("source_conflict_symbols do not match the compatibility manifest")
    if source.mapping_incompatible_symbols != conflicts:
        raise ValueError("mapping_incompatible_symbols do not match the compatibility manifest")

    latest_by_symbol: dict[str, VerifiedCoverageInterval] = {}
    for interval in receipt.coverage:
        current = latest_by_symbol.get(interval.symbol)
        if current is None or (interval.end_ms, interval.start_ms) > (
            current.end_ms,
            current.start_ms,
        ):
            latest_by_symbol[interval.symbol] = interval
    eligible_intervals = {
        symbol: interval
        for symbol, interval in latest_by_symbol.items()
        if compatibility.get(symbol) == "compatible"
        and symbol not in {"BTCUSDT", "ETHUSDT"}
        and interval.end_ms - interval.start_ms >= 730 * _DAY_MS
    }
    eligible = tuple(sorted(eligible_intervals))
    if source.eligible_symbols != eligible:
        raise ValueError("eligible_symbols do not match verified source publications")
    common_start_ms = max(interval.start_ms for interval in eligible_intervals.values())
    common_end_ms = min(interval.end_ms for interval in eligible_intervals.values())
    common_days = (common_end_ms - common_start_ms) // _DAY_MS
    if source.common_complete_days != common_days:
        raise ValueError("common_complete_days do not match verified source publications")
    if source.common_complete_start != _utc_from_ms(common_start_ms):
        raise ValueError("common_complete_start does not match verified source publications")
    if source.common_complete_end != _utc_from_ms(common_end_ms):
        raise ValueError("common_complete_end does not match verified source publications")


def verify_binding_source_universe(
    source: ValidationSourcePreflight,
    bindings: ValidationBindings,
) -> None:
    """Require the bundle's source declaration to match the source-preflight identity."""

    expected = {
        "promotion_receipt_path": source.source_publication_path.as_posix(),
        "promotion_receipt_sha256": source.source_publication_sha256,
        "compatibility_manifest_path": source.compatibility_manifest_path.as_posix(),
        "compatibility_manifest_sha256": source.compatibility_manifest_sha256,
        "eligible_symbols": list(source.eligible_symbols),
        "excluded_source_conflicts": list(source.source_conflict_symbols),
        "common_complete_start": _canonical_utc(source.common_complete_start),
        "common_complete_end": _canonical_utc(source.common_complete_end),
        "common_complete_days_floor": source.common_complete_days,
        "status": "coverage_metadata_sufficient",
    }
    for key, value in expected.items():
        if bindings.source_universe.get(key) != value:
            raise ValueError(f"binding source_universe {key} does not match source preflight")


def main(argv: Sequence[str] | None = None) -> int:
    """Run the explicit-path development-only CLI."""

    args = build_parser().parse_args(argv)
    try:
        summary = _execute(args)
    except (OSError, RuntimeError, ValueError, TypeError) as error:
        print(_safe_error_message(error), file=sys.stderr)
        return 1
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 2 if summary["decision"] == "not_evaluated" else 0


def _execute(args: argparse.Namespace) -> dict[str, object]:
    root = Path(args.repo_root)
    config_path = _repository_relative_path(args.config)
    source_path = _repository_relative_path(args.source_preflight)
    bindings_path = _repository_relative_path(args.bindings)
    config = load_programme_config(root.joinpath(*config_path.parts))
    remote_head = verify_repository_state(root, config)
    verify_preregistration_files(
        root,
        remote_head,
        (config_path, source_path, bindings_path),
    )
    source = load_source_preflight(root.joinpath(*source_path.parts))
    bindings = load_validation_bindings(root.joinpath(*bindings_path.parts))
    if config.dataset_sha256 != source.manifest_sha256:
        raise ValueError("programme dataset identity does not bind the source preflight manifest")
    if source.bindings_sha256 != bindings.bindings_sha256:
        raise ValueError("source preflight does not bind the verified binding bundle")
    for field, digest in bindings.binding_hashes.items():
        if getattr(config, field) != digest:
            raise ValueError(f"programme {field} does not match the verified binding bundle")
    verify_binding_source_universe(source, bindings)
    verify_source_publications(root, source)
    result = publish_validation_preflight_failure(
        config,
        output_root=args.output_root,
        reason_code="missing_prerequisites",
        missing_prerequisites=source.missing_prerequisites,
        source_manifest_sha256=source.manifest_sha256,
    )
    return {
        "programme_id": config.programme_id,
        "config_sha256": config.sha256,
        "execution_status": "failed",
        "decision": "not_evaluated",
        "reason_code": result.reason_code,
        "missing_prerequisites": list(result.missing_prerequisites),
        "programme_receipt_path": str(result.programme_receipt_path),
        "evaluation_receipt_count": len(result.evaluation_receipts),
        "final_holdout_access_count": result.final_holdout_access_count,
        "source_manifest_sha256": result.source_manifest_sha256,
    }


def _load_json_object(path: Path, *, maximum: int) -> dict[str, object]:
    try:
        decoded = json.loads(read_bounded_regular(Path(path), maximum).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError(f"{Path(path).name} is not valid UTF-8 JSON") from error
    if not isinstance(decoded, dict) or any(not isinstance(key, str) for key in decoded):
        raise TypeError(f"{Path(path).name} must contain a JSON object")
    return cast(dict[str, object], decoded)


def _require_string(value: object) -> str:
    if not isinstance(value, str):
        raise TypeError("validation config string field has invalid type")
    return value


def _require_mapping(value: object, label: str) -> Mapping[str, object]:
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise ValueError(f"{label} must be an object with string keys")
    return cast(dict[str, object], value)


def _require_int(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{label} must be a non-negative integer")
    return value


def _require_sha256(value: object, label: str) -> str:
    if not isinstance(value, str) or len(value) != 64 or set(value) - set("0123456789abcdef"):
        raise ValueError(f"{label} must be a lower-case SHA-256")
    return value


def _require_sorted_strings(value: object, label: str) -> tuple[str, ...]:
    if (
        not isinstance(value, list)
        or any(not isinstance(item, str) or not item for item in value)
        or value != sorted(set(value))
    ):
        raise ValueError(f"{label} must be a sorted unique list of non-empty strings")
    return tuple(cast(list[str], value))


def _require_relative_path(value: object) -> PurePosixPath:
    if not isinstance(value, str) or "\\" in value or ":" in value:
        raise ValueError("source_publication_path must be a safe repository-relative POSIX path")
    path = PurePosixPath(value)
    if path.is_absolute() or not path.parts or ".." in path.parts:
        raise ValueError("source_publication_path must be a safe repository-relative POSIX path")
    return path


def _repository_relative_path(value: Path | PurePosixPath) -> PurePosixPath:
    text = value.as_posix()
    if value.is_absolute() or "\\" in text or ":" in text:
        raise ValueError("preregistration paths must be repository-relative")
    path = PurePosixPath(text)
    if not path.parts or ".." in path.parts:
        raise ValueError("preregistration paths must be repository-relative")
    return path


def _require_utc(value: object, label: str) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise ValueError(f"{label} must be a canonical UTC timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError(f"{label} must be a canonical UTC timestamp") from error
    if parsed.tzinfo is None or parsed.utcoffset() != UTC.utcoffset(parsed):
        raise ValueError(f"{label} must be a canonical UTC timestamp")
    if parsed.second or parsed.microsecond:
        raise ValueError(f"{label} must be minute-aligned")
    return parsed


def _utc_from_ms(value: int) -> datetime:
    return datetime.fromtimestamp(value / 1000, UTC)


def _canonical_utc(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _read_verified_promotion_receipt(
    repo_root: Path,
    source: ValidationSourcePreflight,
) -> ReconciliationPromotionReceipt:
    path = Path(repo_root).joinpath(*source.source_publication_path.parts)
    content = read_bounded_regular(path, _MAX_PREFLIGHT_BYTES)
    if hashlib.sha256(content).hexdigest() != source.source_publication_sha256:
        raise ValueError("source publication SHA-256 does not match the preflight manifest")
    with tempfile.TemporaryDirectory(prefix="msl-promotion-receipt-") as directory:
        captured = Path(directory) / "receipt.json"
        captured.write_bytes(content)
        return read_reconciliation_promotion_receipt(captured)


def _read_compatibility_states(
    repo_root: Path,
    source: ValidationSourcePreflight,
) -> dict[str, str]:
    path = Path(repo_root).joinpath(*source.compatibility_manifest_path.parts)
    content = read_bounded_regular(path, _MAX_PREFLIGHT_BYTES)
    if hashlib.sha256(content).hexdigest() != source.compatibility_manifest_sha256:
        raise ValueError("compatibility manifest SHA-256 does not match the source preflight")
    try:
        decoded = json.loads(content)
    except json.JSONDecodeError as error:
        raise ValueError("compatibility manifest is not valid JSON") from error
    if not isinstance(decoded, dict):
        raise ValueError("compatibility manifest must be a JSON object")
    raw = decoded.get("provenance_validation")
    if not isinstance(raw, dict) or not raw:
        raise ValueError("compatibility manifest has no provenance_validation map")
    states: dict[str, str] = {}
    for symbol, state in raw.items():
        if (
            not isinstance(symbol, str)
            or symbol != symbol.upper()
            or state not in {"compatible", "source_conflict"}
        ):
            raise ValueError("compatibility manifest provenance state is invalid")
        states[symbol] = cast(str, state)
    return states


def _git_text(root: Path, *args: str) -> str:
    return _git_bytes(root, *args).decode("utf-8").strip()


def _git_bytes(
    root: Path,
    *args: str,
    maximum: int = _MAX_CONFIG_BYTES,
    timeout_seconds: float = 30.0,
) -> bytes:
    environment = dict(os.environ)
    environment["GIT_TERMINAL_PROMPT"] = "0"
    with tempfile.TemporaryFile() as output:
        try:
            completed = subprocess.run(
                ["git", "-C", str(root), *args],
                check=False,
                stdout=output,
                stderr=subprocess.DEVNULL,
                env=environment,
                timeout=timeout_seconds,
            )
        except subprocess.TimeoutExpired as error:
            command = args[0] if args else "command"
            raise RuntimeError(f"git {command} verification timed out") from error
        output.seek(0, os.SEEK_END)
        size = output.tell()
        if size > maximum:
            command = args[0] if args else "command"
            raise RuntimeError(f"git {command} verification output exceeded its bound")
        output.seek(0)
        content = output.read()
    if completed.returncode != 0:
        command = args[0] if args else "command"
        raise RuntimeError(f"git {command} verification failed")
    return content


def _git_check(root: Path, *args: str) -> bool:
    environment = dict(os.environ)
    environment["GIT_TERMINAL_PROMPT"] = "0"
    try:
        completed = subprocess.run(
            ["git", "-C", str(root), *args],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=environment,
            timeout=30.0,
        )
    except subprocess.TimeoutExpired:
        return False
    return completed.returncode == 0


def _safe_error_message(error: BaseException) -> str:
    message = str(error).replace("\r", " ").replace("\n", " ")
    if _SQL_TOKEN.search(message):
        return "validation operation failed; sensitive database detail was redacted"
    message = _CREDENTIAL_URL.sub(r"\1<redacted>@", message)
    message = _SECRET_ASSIGNMENT.sub(r"\1=<redacted>", message)
    return message[:500] or "validation operation failed"
