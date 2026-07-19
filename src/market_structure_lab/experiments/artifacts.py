"""Canonical immutable terminal trial receipts and legacy experiment readers."""

from __future__ import annotations

import hashlib
import json
import math
import re
import shutil
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path, PurePosixPath

from market_structure_lab.core.artifact_io import (
    bounded_regular_files,
    bounded_subdirectories,
    path_exists_no_follow,
    read_bounded_regular,
    regular_file_matches,
    require_regular_directory,
    sha256_regular,
)

_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$")
_STAGING_TOKEN = re.compile(r"(?:^|[._-])staging(?:$|[._-])", re.IGNORECASE)
_SHA256 = re.compile(r"^[a-f0-9]{64}$")
_CODE_COMMIT = re.compile(r"^[A-Fa-f0-9]{7,64}$")
_RECEIPT_NAME = "receipt.json"
_SCHEMA_VERSION = "trial-receipt-v2"
_LEGACY_SCHEMA_VERSION = "experiment-manifest-v1"
_MAX_RECEIPTS = 100_000
_MAX_ARTIFACTS = 10_000
_MAX_RECEIPT_BYTES = 4 * 1024 * 1024
_METRIC_KINDS = frozenset({"integer", "number", "string", "boolean", "object", "array", "null"})


class ExperimentMode(StrEnum):
    DISCOVERY = "discovery"
    HYPOTHESIS = "hypothesis"
    VALIDATION = "validation"
    STRATEGY = "strategy"


class TerminalStatus(StrEnum):
    FAILED = "failed"
    INCONCLUSIVE = "inconclusive"
    ABANDONED = "abandoned"
    REJECTED = "rejected"
    COMPLETED = "completed"


@dataclass(frozen=True, slots=True)
class ArtifactIdentity:
    identifier: str
    sha256: str

    def __post_init__(self) -> None:
        _require_safe_id(self.identifier, "artifact identifier")
        _require_sha256(self.sha256, "artifact sha256")

    def to_dict(self) -> dict[str, str]:
        return {"id": self.identifier, "sha256": self.sha256}


@dataclass(frozen=True, slots=True)
class TrialRange:
    symbol: str
    timeframe: str
    start: str
    end: str

    def __post_init__(self) -> None:
        _require_safe_id(self.symbol, "range symbol")
        _require_safe_id(self.timeframe, "range timeframe")
        start = _canonical_utc(self.start, "range start")
        end = _canonical_utc(self.end, "range end")
        if start >= end:
            raise ValueError("trial range start must precede end")

    def to_dict(self) -> dict[str, str]:
        return {
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "start": self.start,
            "end": self.end,
        }


@dataclass(frozen=True, slots=True)
class ExperimentConfig:
    run_id: str
    mode: ExperimentMode
    dataset_snapshot: ArtifactIdentity
    feature_publication: ArtifactIdentity
    feature_registry: ArtifactIdentity
    normalizer: ArtifactIdentity
    frozen_split: Mapping[str, object]
    detector_version: str
    candidate_id: str | None
    candidate_version: str | None
    code_commit: str
    lock_sha256: str
    canonical_config: Mapping[str, object]
    seed: int
    symbols: tuple[str, ...]
    timeframes: tuple[str, ...]
    ranges: tuple[TrialRange, ...]
    parent_ids: tuple[str, ...]
    metrics_schema: Mapping[str, str]
    hypothesis: str | None = None
    outcome_policy: ArtifactIdentity | None = None
    cost_policy: ArtifactIdentity | None = None

    def __post_init__(self) -> None:
        _require_safe_id(self.run_id, "run_id")
        if _STAGING_TOKEN.search(self.run_id):
            raise ValueError("run_id contains the reserved staging path token")
        if not isinstance(self.mode, ExperimentMode):
            raise TypeError("mode must be an ExperimentMode")
        for identity, label in (
            (self.dataset_snapshot, "dataset_snapshot"),
            (self.feature_publication, "feature_publication"),
            (self.feature_registry, "feature_registry"),
            (self.normalizer, "normalizer"),
        ):
            if not isinstance(identity, ArtifactIdentity):
                raise TypeError(f"{label} must be an ArtifactIdentity")
        _require_mapping(self.frozen_split, "frozen_split")
        _require_text(self.detector_version, "detector_version")
        _require_pattern(self.code_commit, _CODE_COMMIT, "code_commit")
        _require_sha256(self.lock_sha256, "lock_sha256")
        _require_mapping(self.canonical_config, "canonical_config")
        if isinstance(self.seed, bool) or not isinstance(self.seed, int):
            raise ValueError("seed must be an explicit integer")
        _require_unique_ids(self.symbols, "symbols")
        _require_unique_ids(self.timeframes, "timeframes")
        if not self.ranges or any(not isinstance(item, TrialRange) for item in self.ranges):
            raise ValueError("ranges must contain at least one TrialRange")
        if tuple(sorted(self.ranges, key=_range_key)) != self.ranges:
            raise ValueError("ranges must use canonical order")
        if {item.symbol for item in self.ranges} != set(self.symbols):
            raise ValueError("range symbols must exactly match symbols")
        if {item.timeframe for item in self.ranges} != set(self.timeframes):
            raise ValueError("range timeframes must exactly match timeframes")
        _require_unique_ids(self.parent_ids, "parent_ids", allow_empty=True)
        if self.run_id in self.parent_ids:
            raise ValueError("parent_ids cannot contain run_id")
        _require_metrics_schema(self.metrics_schema)
        if self.mode is ExperimentMode.DISCOVERY:
            if self.candidate_id is not None or self.candidate_version is not None:
                raise ValueError("discovery mode cannot claim candidate linkage")
        else:
            _require_text(self.hypothesis, "hypothesis")
            _require_safe_id(self.candidate_id, "candidate_id")
            _require_text(self.candidate_version, "candidate_version")
        if self.mode in {ExperimentMode.VALIDATION, ExperimentMode.STRATEGY}:
            if not isinstance(self.outcome_policy, ArtifactIdentity):
                raise ValueError("outcome_policy is required for validation and strategy")
            if not isinstance(self.cost_policy, ArtifactIdentity):
                raise ValueError("cost_policy is required for validation and strategy")
        elif self.outcome_policy is not None or self.cost_policy is not None:
            raise ValueError("outcome_policy and cost_policy are forbidden before validation")

    def identity_dict(self) -> dict[str, object]:
        return {
            "run_id": self.run_id,
            "mode": self.mode.value,
            "dataset_snapshot": self.dataset_snapshot.to_dict(),
            "feature_publication": self.feature_publication.to_dict(),
            "feature_registry": self.feature_registry.to_dict(),
            "normalizer": self.normalizer.to_dict(),
            "frozen_split": dict(self.frozen_split),
            "detector_version": self.detector_version,
            "candidate_id": self.candidate_id,
            "candidate_version": self.candidate_version,
            "code_commit": self.code_commit.lower(),
            "lock_sha256": self.lock_sha256,
            "canonical_config": dict(self.canonical_config),
            "seed": self.seed,
            "symbols": list(self.symbols),
            "timeframes": list(self.timeframes),
            "ranges": [item.to_dict() for item in self.ranges],
            "parent_ids": list(self.parent_ids),
            "metrics_schema": dict(self.metrics_schema),
            "hypothesis": self.hypothesis,
            "outcome_policy": (
                None if self.outcome_policy is None else self.outcome_policy.to_dict()
            ),
            "cost_policy": None if self.cost_policy is None else self.cost_policy.to_dict(),
        }


@dataclass(frozen=True, slots=True)
class TrialManifest:
    schema_version: str
    run_id: str
    mode: ExperimentMode
    status: TerminalStatus
    identity_sha256: str
    dataset_snapshot: ArtifactIdentity
    feature_publication: ArtifactIdentity
    feature_registry: ArtifactIdentity
    normalizer: ArtifactIdentity
    frozen_split: Mapping[str, object]
    detector_version: str
    candidate_id: str | None
    candidate_version: str | None
    code_commit: str
    lock_sha256: str
    canonical_config: Mapping[str, object]
    seed: int
    symbols: tuple[str, ...]
    timeframes: tuple[str, ...]
    ranges: tuple[TrialRange, ...]
    parent_ids: tuple[str, ...]
    metrics_schema: Mapping[str, str]
    hypothesis: str | None
    outcome_policy: ArtifactIdentity | None
    cost_policy: ArtifactIdentity | None
    warnings: tuple[str, ...]
    conclusion: str
    started_at: str
    completed_at: str
    artifact_sha256: tuple[tuple[str, str], ...]
    receipt_sha256: str

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "run_id": self.run_id,
            "mode": self.mode.value,
            "status": self.status.value,
            "identity_sha256": self.identity_sha256,
            "dataset_snapshot": self.dataset_snapshot.to_dict(),
            "feature_publication": self.feature_publication.to_dict(),
            "feature_registry": self.feature_registry.to_dict(),
            "normalizer": self.normalizer.to_dict(),
            "frozen_split": dict(self.frozen_split),
            "detector_version": self.detector_version,
            "candidate_id": self.candidate_id,
            "candidate_version": self.candidate_version,
            "code_commit": self.code_commit,
            "lock_sha256": self.lock_sha256,
            "canonical_config": dict(self.canonical_config),
            "seed": self.seed,
            "symbols": list(self.symbols),
            "timeframes": list(self.timeframes),
            "ranges": [item.to_dict() for item in self.ranges],
            "parent_ids": list(self.parent_ids),
            "metrics_schema": dict(self.metrics_schema),
            "hypothesis": self.hypothesis,
            "outcome_policy": (
                None if self.outcome_policy is None else self.outcome_policy.to_dict()
            ),
            "cost_policy": None if self.cost_policy is None else self.cost_policy.to_dict(),
            "warnings": list(self.warnings),
            "conclusion": self.conclusion,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "artifact_sha256": dict(self.artifact_sha256),
            "receipt_sha256": self.receipt_sha256,
        }


@dataclass(frozen=True, slots=True)
class ExperimentResult:
    path: Path
    manifest: TrialManifest


@dataclass(frozen=True, slots=True)
class TrialOutput:
    status: TerminalStatus
    metrics: Mapping[str, object]
    conclusion: str
    warnings: tuple[str, ...] = ()
    artifacts: Mapping[str, str | bytes] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class LegacyExperimentResult:
    path: Path
    schema_version: str
    run_id: str
    mode: ExperimentMode
    legacy: bool = True
    dataset_snapshot: None = None


class TrialAbandoned(RuntimeError):
    """Signal an explicit abandoned attempt while preserving the original exception."""


class TrialArtifactBudgetExceeded(ValueError):
    """A trial bundle exceeded a caller-frozen byte or entry admission limit."""

    def __init__(self, message: str, *, observed: int, limit: int) -> None:
        super().__init__(message)
        self.observed = observed
        self.limit = limit


def save_experiment_result(
    *,
    config: ExperimentConfig,
    status: TerminalStatus,
    metrics: Mapping[str, object],
    conclusion: str,
    started_at: datetime,
    completed_at: datetime,
    warnings: Sequence[str] = (),
    artifacts: Mapping[str, str | bytes] | None = None,
    root: str | Path = "data/exports/trials",
    maximum_total_bytes: int | None = None,
    maximum_entries: int | None = None,
) -> ExperimentResult:
    """Atomically publish one immutable terminal receipt and its exact artifacts."""

    if not isinstance(config, ExperimentConfig):
        raise TypeError("config must be an ExperimentConfig")
    if not isinstance(status, TerminalStatus):
        raise TypeError("status must be a TerminalStatus")
    _validate_metrics(metrics, config.metrics_schema, status)
    _require_text(conclusion, "conclusion")
    canonical_warnings = _validated_warnings(warnings)
    started = _canonical_datetime(started_at, "started_at")
    completed = _canonical_datetime(completed_at, "completed_at")
    if started > completed:
        raise ValueError("started_at cannot be after completed_at")
    payloads: dict[str, bytes] = {"metrics.json": _json_file(dict(metrics))}
    for name, content in (artifacts or {}).items():
        canonical_name = _artifact_name(name)
        if canonical_name in payloads or canonical_name == _RECEIPT_NAME:
            raise ValueError(f"duplicate or reserved artifact path: {canonical_name}")
        payloads[canonical_name] = content.encode("utf-8") if isinstance(content, str) else content
        if not isinstance(payloads[canonical_name], bytes):
            raise TypeError("artifact content must be str or bytes")
    if len(payloads) > _MAX_ARTIFACTS:
        raise ValueError("trial artifact count exceeds the bounded limit")
    for value, label in (
        (maximum_total_bytes, "maximum_total_bytes"),
        (maximum_entries, "maximum_entries"),
    ):
        if value is not None and (
            isinstance(value, bool) or not isinstance(value, int) or value < 1
        ):
            raise ValueError(f"{label} must be a positive integer or None")

    artifact_hashes = tuple(sorted((name, _sha256(content)) for name, content in payloads.items()))
    identity = config.identity_dict()
    manifest_values: dict[str, object] = {
        "schema_version": _SCHEMA_VERSION,
        **identity,
        "status": status.value,
        "identity_sha256": _sha256(_canonical_json(identity)),
        "warnings": list(canonical_warnings),
        "conclusion": conclusion.strip(),
        "started_at": started,
        "completed_at": completed,
        "artifact_sha256": dict(artifact_hashes),
    }
    manifest_values["receipt_sha256"] = _sha256(_canonical_json(manifest_values))
    manifest = _manifest_from_dict(manifest_values)
    payloads[_RECEIPT_NAME] = _json_file(manifest.to_dict())
    if maximum_entries is not None and len(payloads) > maximum_entries:
        raise TrialArtifactBudgetExceeded(
            "trial bundle entries exceed the frozen limit",
            observed=len(payloads),
            limit=maximum_entries,
        )
    total_bytes = sum(len(content) for content in payloads.values())
    if maximum_total_bytes is not None and total_bytes > maximum_total_bytes:
        raise TrialArtifactBudgetExceeded(
            "trial bundle bytes exceed the frozen limit",
            observed=total_bytes,
            limit=maximum_total_bytes,
        )
    root_path = Path(root)
    if not path_exists_no_follow(root_path):
        root_path.mkdir(parents=True)
    require_regular_directory(root_path)
    final_dir = root_path / config.run_id
    stage_dir = root_path / f".{config.run_id}.staging"
    if path_exists_no_follow(stage_dir):
        raise RuntimeError(f"stale trial staging directory exists: {stage_dir}")
    if path_exists_no_follow(final_dir):
        existing = verify_trial_receipt(final_dir)
        if existing.identity_sha256 != manifest.identity_sha256:
            raise RuntimeError("trial run identity conflict")
        _require_identical_payloads(final_dir, payloads)
        return ExperimentResult(final_dir, existing)

    stage_dir.mkdir()
    try:
        for name, content in payloads.items():
            path = stage_dir / PurePosixPath(name)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
        verify_trial_receipt(stage_dir)
        stage_dir.replace(final_dir)
    except Exception:
        if path_exists_no_follow(stage_dir):
            try:
                require_regular_directory(stage_dir)
            except RuntimeError:
                pass
            else:
                shutil.rmtree(stage_dir)
        raise
    return ExperimentResult(final_dir, manifest)


def execute_trial_attempt(
    *,
    config: ExperimentConfig,
    algorithm: Callable[[], TrialOutput],
    root: str | Path = "data/exports/trials",
    started_at: datetime | None = None,
    completed_at: Callable[[], datetime] | None = None,
) -> ExperimentResult:
    """Execute one algorithm and durably classify failure or abandonment before reraising."""

    _preflight_trial_attempt(config, root)
    start = started_at or datetime.now(tz=UTC).replace(microsecond=0)
    completion_clock = completed_at or (lambda: datetime.now(tz=UTC).replace(microsecond=0))
    try:
        output = algorithm()
        if not isinstance(output, TrialOutput):
            raise TypeError("algorithm must return TrialOutput")
        return save_experiment_result(
            config=config,
            status=output.status,
            metrics=output.metrics,
            conclusion=output.conclusion,
            warnings=output.warnings,
            artifacts=output.artifacts,
            started_at=start,
            completed_at=completion_clock(),
            root=root,
        )
    except Exception as error:
        status = (
            TerminalStatus.ABANDONED if isinstance(error, TrialAbandoned) else TerminalStatus.FAILED
        )
        try:
            save_experiment_result(
                config=config,
                status=status,
                metrics={},
                conclusion=f"Trial execution raised {type(error).__name__}.",
                warnings=("algorithm or output publication did not complete",),
                started_at=start,
                completed_at=completion_clock(),
                root=root,
            )
        except Exception as publication_error:
            error.add_note(
                "terminal trial receipt publication also failed: "
                f"{type(publication_error).__name__}"
            )
        raise


def verify_trial_receipt(directory: str | Path) -> TrialManifest:
    """Verify canonical receipt bytes, exact file set, and every artifact checksum."""

    path = Path(directory)
    require_regular_directory(path)
    receipt_path = path / _RECEIPT_NAME
    try:
        receipt_bytes = read_bounded_regular(receipt_path, _MAX_RECEIPT_BYTES)
        raw = json.loads(receipt_bytes.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise RuntimeError("trial receipt is missing or malformed") from error
    if not isinstance(raw, dict):
        raise RuntimeError("trial receipt is malformed")
    try:
        receipt_sha256 = raw.pop("receipt_sha256")
        if not isinstance(receipt_sha256, str) or _sha256(_canonical_json(raw)) != receipt_sha256:
            raise RuntimeError("trial receipt checksum tamper detected")
        raw["receipt_sha256"] = receipt_sha256
        manifest = _manifest_from_dict(raw)
    except (KeyError, TypeError, ValueError) as error:
        raise RuntimeError("trial receipt contract is malformed") from error
    if path.name not in {manifest.run_id, f".{manifest.run_id}.staging"}:
        raise RuntimeError("trial receipt path identity does not match run_id")
    expected = {_RECEIPT_NAME, *(name for name, _ in manifest.artifact_sha256)}
    actual = set(bounded_regular_files(path, maximum=_MAX_ARTIFACTS + 1))
    if actual != expected:
        raise RuntimeError("trial receipt contains missing or unexpected files")
    for name, expected_sha in manifest.artifact_sha256:
        if sha256_regular(path / PurePosixPath(name)) != expected_sha:
            raise RuntimeError("trial receipt artifact tamper detected")
    metrics_path = path / "metrics.json"
    try:
        metrics_bytes = read_bounded_regular(metrics_path, _MAX_RECEIPT_BYTES)
        metrics = json.loads(metrics_bytes.decode("utf-8"))
        if not isinstance(metrics, dict):
            raise ValueError("metrics must be an object")
        if metrics_bytes != _json_file(metrics):
            raise RuntimeError("trial metrics JSON is not canonical")
        _validate_metrics(metrics, manifest.metrics_schema, manifest.status)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError) as error:
        raise RuntimeError("trial metrics contract is malformed") from error
    if receipt_bytes != _json_file(manifest.to_dict()):
        raise RuntimeError("trial receipt is not canonical JSON")
    return manifest


def read_trial_ledger(root: str | Path) -> tuple[TrialManifest, ...]:
    """Reconstruct terminal trials only from verified immutable canonical receipts."""

    path = Path(root)
    if not path_exists_no_follow(path):
        return ()
    children = bounded_subdirectories(path, maximum=_MAX_RECEIPTS)
    receipts: list[TrialManifest] = []
    seen: set[str] = set()
    for child in children:
        if child.name.startswith(".") or _STAGING_TOKEN.search(child.name):
            raise RuntimeError("trial ledger contains a hidden or staging-like entry")
        receipt = verify_trial_receipt(child)
        if receipt.run_id in seen:
            raise RuntimeError("trial ledger contains conflicting duplicate run IDs")
        seen.add(receipt.run_id)
        receipts.append(receipt)
    return tuple(receipts)


def _preflight_trial_attempt(config: ExperimentConfig, root: str | Path) -> None:
    if not isinstance(config, ExperimentConfig):
        raise TypeError("config must be an ExperimentConfig")
    expected_identity = _sha256(_canonical_json(config.identity_dict()))
    for receipt in read_trial_ledger(root):
        if receipt.run_id == config.run_id and receipt.identity_sha256 != expected_identity:
            raise RuntimeError("trial run identity conflict")


def read_experiment_result(directory: str | Path) -> TrialManifest | LegacyExperimentResult:
    """Read canonical receipts or verified v1 bundles without inventing provenance."""

    path = Path(directory)
    files = set(bounded_regular_files(path, maximum=_MAX_ARTIFACTS + 1))
    if _RECEIPT_NAME in files:
        return verify_trial_receipt(path)
    manifest_path = path / "manifest.json"
    try:
        raw = json.loads(read_bounded_regular(manifest_path, _MAX_RECEIPT_BYTES).decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise RuntimeError("experiment result is missing or malformed") from error
    if not isinstance(raw, dict) or raw.get("schema_version") != _LEGACY_SCHEMA_VERSION:
        raise RuntimeError("unsupported experiment result schema")
    hashes = raw.get("artifact_sha256")
    if not isinstance(hashes, dict):
        raise RuntimeError("legacy experiment manifest is malformed")
    for name, sha256 in hashes.items():
        artifact = path / PurePosixPath(_artifact_name(name))
        if (
            not isinstance(sha256, str)
            or _artifact_name(name) not in files
            or sha256_regular(artifact) != sha256
        ):
            raise RuntimeError("legacy experiment artifact tamper detected")
    return LegacyExperimentResult(
        path=path,
        schema_version=_LEGACY_SCHEMA_VERSION,
        run_id=_required_str(raw, "run_id"),
        mode=ExperimentMode(_required_str(raw, "mode")),
    )


def _manifest_from_dict(raw: Mapping[str, object]) -> TrialManifest:
    expected = {
        "schema_version",
        "run_id",
        "mode",
        "status",
        "identity_sha256",
        "dataset_snapshot",
        "feature_publication",
        "feature_registry",
        "normalizer",
        "frozen_split",
        "detector_version",
        "candidate_id",
        "candidate_version",
        "code_commit",
        "lock_sha256",
        "canonical_config",
        "seed",
        "symbols",
        "timeframes",
        "ranges",
        "parent_ids",
        "metrics_schema",
        "hypothesis",
        "outcome_policy",
        "cost_policy",
        "warnings",
        "conclusion",
        "started_at",
        "completed_at",
        "artifact_sha256",
        "receipt_sha256",
    }
    if set(raw) != expected or raw.get("schema_version") != _SCHEMA_VERSION:
        raise ValueError("unsupported trial receipt schema")
    ranges_raw = _required_list(raw, "ranges")
    manifest = TrialManifest(
        schema_version=_SCHEMA_VERSION,
        run_id=_required_str(raw, "run_id"),
        mode=ExperimentMode(_required_str(raw, "mode")),
        status=TerminalStatus(_required_str(raw, "status")),
        identity_sha256=_required_str(raw, "identity_sha256"),
        dataset_snapshot=_identity_from_object(raw["dataset_snapshot"]),
        feature_publication=_identity_from_object(raw["feature_publication"]),
        feature_registry=_identity_from_object(raw["feature_registry"]),
        normalizer=_identity_from_object(raw["normalizer"]),
        frozen_split=_required_mapping(raw, "frozen_split"),
        detector_version=_required_str(raw, "detector_version"),
        candidate_id=_optional_str(raw["candidate_id"]),
        candidate_version=_optional_str(raw["candidate_version"]),
        code_commit=_required_str(raw, "code_commit"),
        lock_sha256=_required_str(raw, "lock_sha256"),
        canonical_config=_required_mapping(raw, "canonical_config"),
        seed=_required_int(raw, "seed"),
        symbols=tuple(_string_list(raw, "symbols")),
        timeframes=tuple(_string_list(raw, "timeframes")),
        ranges=tuple(_range_from_object(item) for item in ranges_raw),
        parent_ids=tuple(_string_list(raw, "parent_ids")),
        metrics_schema={
            str(key): str(value) for key, value in _required_mapping(raw, "metrics_schema").items()
        },
        hypothesis=_optional_str(raw["hypothesis"]),
        outcome_policy=_optional_identity(raw["outcome_policy"]),
        cost_policy=_optional_identity(raw["cost_policy"]),
        warnings=tuple(_string_list(raw, "warnings")),
        conclusion=_required_str(raw, "conclusion"),
        started_at=_required_str(raw, "started_at"),
        completed_at=_required_str(raw, "completed_at"),
        artifact_sha256=tuple(
            sorted(
                (str(key), str(value))
                for key, value in _required_mapping(raw, "artifact_sha256").items()
            )
        ),
        receipt_sha256=_required_str(raw, "receipt_sha256"),
    )
    config = ExperimentConfig(
        run_id=manifest.run_id,
        mode=manifest.mode,
        dataset_snapshot=manifest.dataset_snapshot,
        feature_publication=manifest.feature_publication,
        feature_registry=manifest.feature_registry,
        normalizer=manifest.normalizer,
        frozen_split=manifest.frozen_split,
        detector_version=manifest.detector_version,
        candidate_id=manifest.candidate_id,
        candidate_version=manifest.candidate_version,
        code_commit=manifest.code_commit,
        lock_sha256=manifest.lock_sha256,
        canonical_config=manifest.canonical_config,
        seed=manifest.seed,
        symbols=manifest.symbols,
        timeframes=manifest.timeframes,
        ranges=manifest.ranges,
        parent_ids=manifest.parent_ids,
        metrics_schema=manifest.metrics_schema,
        hypothesis=manifest.hypothesis,
        outcome_policy=manifest.outcome_policy,
        cost_policy=manifest.cost_policy,
    )
    _require_sha256(manifest.identity_sha256, "identity_sha256")
    if _sha256(_canonical_json(config.identity_dict())) != manifest.identity_sha256:
        raise ValueError("trial identity checksum mismatch")
    _canonical_utc(manifest.started_at, "started_at")
    _canonical_utc(manifest.completed_at, "completed_at")
    if manifest.started_at > manifest.completed_at:
        raise ValueError("started_at cannot be after completed_at")
    _validated_warnings(manifest.warnings)
    _require_sha256(manifest.receipt_sha256, "receipt_sha256")
    for name, sha256 in manifest.artifact_sha256:
        _artifact_name(name)
        _require_sha256(sha256, "artifact sha256")
    return manifest


def _require_identical_payloads(directory: Path, payloads: Mapping[str, bytes]) -> None:
    for name, proposed in payloads.items():
        path = directory / PurePosixPath(name)
        try:
            identical = regular_file_matches(path, proposed)
        except (OSError, RuntimeError):
            identical = False
        if not identical:
            raise RuntimeError("trial run content conflict")


def _validate_metrics(
    metrics: Mapping[str, object],
    schema: Mapping[str, str],
    status: TerminalStatus,
) -> None:
    if not isinstance(metrics, Mapping):
        raise TypeError("metrics must be a mapping")
    if status in {TerminalStatus.FAILED, TerminalStatus.ABANDONED} and not metrics:
        _canonical_json(dict(metrics))
        return
    if set(metrics) != set(schema):
        raise ValueError("metrics keys must exactly match metrics_schema")
    for name, kind in schema.items():
        if not _metric_matches_kind(metrics[name], kind):
            raise ValueError(f"metric {name} must match metrics_schema kind {kind}")
    _canonical_json(dict(metrics))


def _require_metrics_schema(value: Mapping[str, str]) -> None:
    if not isinstance(value, Mapping) or not value:
        raise ValueError("metrics_schema must be a non-empty mapping")
    for key, kind in value.items():
        _require_safe_id(key, "metrics_schema key")
        _require_text(kind, "metrics_schema value")
        if kind not in _METRIC_KINDS:
            raise ValueError("metrics_schema kind is unsupported")
    _canonical_json(dict(value))


def _metric_matches_kind(value: object, kind: str) -> bool:
    if kind == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if kind == "number":
        return (
            isinstance(value, (int, float))
            and not isinstance(value, bool)
            and math.isfinite(float(value))
        )
    if kind == "string":
        return isinstance(value, str)
    if kind == "boolean":
        return isinstance(value, bool)
    if kind == "object":
        return isinstance(value, Mapping)
    if kind == "array":
        return isinstance(value, (list, tuple))
    return kind == "null" and value is None


def _require_mapping(value: Mapping[str, object], label: str) -> None:
    if not isinstance(value, Mapping) or not value:
        raise ValueError(f"{label} must be a non-empty mapping")
    _canonical_json(dict(value))


def _canonical_json(value: object) -> bytes:
    try:
        return json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (TypeError, ValueError) as error:
        raise ValueError("trial values must be finite canonical JSON") from error


def _json_file(value: object) -> bytes:
    return _canonical_json(value) + b"\n"


def _artifact_name(value: object) -> str:
    if not isinstance(value, str) or not value or "\\" in value:
        raise ValueError("artifact path must be a safe relative POSIX path")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError("artifact path must be a safe relative POSIX path")
    if any(_SAFE_ID.fullmatch(part) is None for part in path.parts):
        raise ValueError("artifact path contains an unsafe name")
    return path.as_posix()


def _canonical_datetime(value: datetime, label: str) -> str:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{label} must be timezone-aware")
    if value.utcoffset() != UTC.utcoffset(value):
        raise ValueError(f"{label} must be UTC")
    if value.microsecond:
        raise ValueError(f"{label} must use whole-second precision")
    return value.isoformat().replace("+00:00", "Z")


def _canonical_utc(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise ValueError(f"{label} must be canonical UTC")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as error:
        raise ValueError(f"{label} must be canonical UTC") from error
    if _canonical_datetime(parsed, label) != value:
        raise ValueError(f"{label} must use canonical second-precision UTC")
    return value


def _validated_warnings(values: Sequence[str]) -> tuple[str, ...]:
    if isinstance(values, (str, bytes)):
        raise ValueError("warnings must be a sequence")
    result = tuple(values)
    if any(not isinstance(item, str) or not item.strip() for item in result):
        raise ValueError("warnings must contain non-empty strings")
    return result


def _require_unique_ids(values: tuple[str, ...], label: str, *, allow_empty: bool = False) -> None:
    if (
        not isinstance(values, tuple)
        or (not values and not allow_empty)
        or len(set(values)) != len(values)
    ):
        raise ValueError(f"{label} must be a unique tuple")
    if tuple(sorted(values)) != values:
        raise ValueError(f"{label} must use canonical order")
    for value in values:
        _require_safe_id(value, label)


def _require_safe_id(value: object, label: str) -> str:
    return _require_pattern(value, _SAFE_ID, label)


def _require_sha256(value: object, label: str) -> str:
    return _require_pattern(value, _SHA256, label)


def _require_pattern(value: object, pattern: re.Pattern[str], label: str) -> str:
    if not isinstance(value, str) or pattern.fullmatch(value) is None:
        raise ValueError(f"{label} has an invalid canonical format")
    return value


def _require_text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be non-empty")
    return value


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _range_key(value: TrialRange) -> tuple[str, str, str, str]:
    return (value.symbol, value.timeframe, value.start, value.end)


def _identity_from_object(value: object) -> ArtifactIdentity:
    if not isinstance(value, Mapping) or set(value) != {"id", "sha256"}:
        raise ValueError("artifact identity is malformed")
    return ArtifactIdentity(str(value["id"]), str(value["sha256"]))


def _optional_identity(value: object) -> ArtifactIdentity | None:
    return None if value is None else _identity_from_object(value)


def _range_from_object(value: object) -> TrialRange:
    if not isinstance(value, Mapping) or set(value) != {"symbol", "timeframe", "start", "end"}:
        raise ValueError("trial range is malformed")
    return TrialRange(
        symbol=str(value["symbol"]),
        timeframe=str(value["timeframe"]),
        start=str(value["start"]),
        end=str(value["end"]),
    )


def _required_str(raw: Mapping[str, object], key: str) -> str:
    value = raw.get(key)
    if not isinstance(value, str):
        raise ValueError(f"{key} must be a string")
    return value


def _optional_str(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("optional value must be a string or null")
    return value


def _required_int(raw: Mapping[str, object], key: str) -> int:
    value = raw.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{key} must be an integer")
    return value


def _required_mapping(raw: Mapping[str, object], key: str) -> Mapping[str, object]:
    value = raw.get(key)
    if not isinstance(value, Mapping):
        raise ValueError(f"{key} must be an object")
    return value


def _required_list(raw: Mapping[str, object], key: str) -> list[object]:
    value = raw.get(key)
    if not isinstance(value, list):
        raise ValueError(f"{key} must be a list")
    return value


def _string_list(raw: Mapping[str, object], key: str) -> list[str]:
    values = _required_list(raw, key)
    if any(not isinstance(item, str) for item in values):
        raise ValueError(f"{key} must contain strings")
    return [str(item) for item in values]


__all__ = [
    "ArtifactIdentity",
    "ExperimentConfig",
    "ExperimentMode",
    "ExperimentResult",
    "LegacyExperimentResult",
    "TerminalStatus",
    "TrialAbandoned",
    "TrialManifest",
    "TrialOutput",
    "TrialRange",
    "execute_trial_attempt",
    "read_experiment_result",
    "read_trial_ledger",
    "save_experiment_result",
    "verify_trial_receipt",
]
