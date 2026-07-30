"""Run one preregistered Phase 5 development-only validation programme."""

from __future__ import annotations

import argparse
from dataclasses import dataclass, fields
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys
from collections.abc import Sequence
from typing import cast

from market_structure_lab.core.artifact_io import read_bounded_regular
from market_structure_lab.core.identity import hash_json
from market_structure_lab.research.models import (
    VALIDATION_SLOT_ROSTER,
    ValidationProgrammeConfig,
    ValidationWorkBudget,
)
from market_structure_lab.research.validation import publish_validation_preflight_failure

_MAX_CONFIG_BYTES = 16 * 1024 * 1024
_MAX_PREFLIGHT_BYTES = 1024 * 1024
_SOURCE_SCHEMA = "phase5-validation-source-preflight-v1"
_SOURCE_DOMAIN = "phase5-validation-source-preflight"
_SOURCE_KEYS = {
    "schema_version",
    "component",
    "status",
    "final_holdout_access_count",
    "eligible_symbols",
    "source_conflict_symbols",
    "mapping_incompatible_symbols",
    "common_complete_days",
    "source_publication_path",
    "source_publication_sha256",
    "missing_prerequisites",
    "manifest_sha256",
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
    source_publication_path: PurePosixPath
    source_publication_sha256: str
    missing_prerequisites: tuple[str, ...]
    manifest_sha256: str
    component: str = "development"
    status: str = "missing_prerequisites"
    final_holdout_access_count: int = 0
    schema_version: str = _SOURCE_SCHEMA


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
    missing = _require_sorted_strings(payload["missing_prerequisites"], "missing_prerequisites")
    if not missing:
        raise ValueError("missing_prerequisites must not be empty")
    relative_path = _require_relative_path(payload["source_publication_path"])
    source_sha256 = _require_sha256(
        payload["source_publication_sha256"], "source_publication_sha256"
    )
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
        source_publication_path=relative_path,
        source_publication_sha256=source_sha256,
        missing_prerequisites=missing,
        manifest_sha256=recorded_sha256,
    )


def build_parser() -> argparse.ArgumentParser:
    """Build the explicit-path Task 10 CLI parser."""

    parser = argparse.ArgumentParser(
        description="Publish one bounded Phase 5 development-only validation result"
    )
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--source-preflight", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--repo-root", type=Path, required=True)
    return parser


def verify_repository_state(repo_root: Path, config: ValidationProgrammeConfig) -> None:
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


def verify_source_publication(
    repo_root: Path,
    source: ValidationSourcePreflight,
) -> None:
    """Verify the bounded source-universe receipt named by the preflight manifest."""

    publication = Path(repo_root).joinpath(*source.source_publication_path.parts)
    observed = hashlib.sha256(read_bounded_regular(publication, _MAX_PREFLIGHT_BYTES)).hexdigest()
    if observed != source.source_publication_sha256:
        raise ValueError("source publication SHA-256 does not match the preflight manifest")


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
    config = load_programme_config(args.config)
    source = load_source_preflight(args.source_preflight)
    if config.dataset_sha256 != source.manifest_sha256:
        raise ValueError("programme dataset identity does not bind the source preflight manifest")
    verify_repository_state(args.repo_root, config)
    verify_source_publication(args.repo_root, source)
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


def _git_text(root: Path, *args: str) -> str:
    return _git_bytes(root, *args).decode("utf-8").strip()


def _git_bytes(root: Path, *args: str) -> bytes:
    completed = subprocess.run(
        ["git", "-C", str(root), *args],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )
    if completed.returncode != 0:
        command = args[0] if args else "command"
        raise RuntimeError(f"git {command} verification failed")
    return completed.stdout


def _git_check(root: Path, *args: str) -> bool:
    completed = subprocess.run(
        ["git", "-C", str(root), *args],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return completed.returncode == 0


def _safe_error_message(error: BaseException) -> str:
    message = str(error).replace("\r", " ").replace("\n", " ")
    if _SQL_TOKEN.search(message):
        return "validation operation failed; sensitive database detail was redacted"
    message = _CREDENTIAL_URL.sub(r"\1<redacted>@", message)
    message = _SECRET_ASSIGNMENT.sub(r"\1=<redacted>", message)
    return message[:500] or "validation operation failed"
