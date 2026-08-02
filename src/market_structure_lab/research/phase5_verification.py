"""Deterministic, cross-platform repository verification manifests for Phase 5."""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
import hashlib
import platform
import re
import sys
from typing import cast

from market_structure_lab.research.validation_v2_models import publication_json_bytes

_SCHEMA_VERSION = "phase5-repository-verification-v1"
_WINDOWS_RUNNER_FILE = "tests/test_daily_candle_refresh_runner.py"
_RESULT_STATUSES = frozenset({"passed", "failed", "skipped"})


@dataclass(frozen=True)
class VerificationEnvironment:
    """Interpreter and operating-system identity bound to one verification action."""

    python_version: str
    python_implementation: str
    executable: str
    operating_system: str
    platform: str

    @classmethod
    def current(cls) -> VerificationEnvironment:
        """Capture the current interpreter and operating-system identity."""

        operating_system = platform.system()
        platform_identity = ";".join(
            (
                f"system={operating_system}",
                f"release={platform.release()}",
                f"version={platform.version()}",
                f"machine={platform.machine()}",
            )
        )
        return cls(
            python_version=platform.python_version(),
            python_implementation=platform.python_implementation(),
            executable=sys.executable,
            operating_system=operating_system,
            platform=platform_identity,
        )

    def to_dict(self) -> dict[str, str]:
        return {
            "python_version": self.python_version,
            "python_implementation": self.python_implementation,
            "executable": self.executable,
            "operating_system": self.operating_system,
            "platform": self.platform,
        }


def sha256_bytes(content: bytes) -> str:
    """Return the lowercase SHA-256 digest for exact bytes."""

    return hashlib.sha256(content).hexdigest()


def utc_now() -> str:
    """Return a microsecond UTC timestamp suitable for evidence manifests."""

    return datetime.now(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def nodeids_bytes(nodeids: Sequence[str]) -> bytes:
    """Encode an ordered node-id inventory without platform-specific newlines."""

    return "".join(f"{nodeid}\n" for nodeid in nodeids).encode("utf-8")


def parse_collected_nodeids(output: str) -> tuple[str, ...]:
    """Extract the exact ordered pytest node-id inventory from quiet collection output."""

    nodeids = tuple(line.strip() for line in output.splitlines() if "::" in line)
    if not nodeids or len(nodeids) != len(set(nodeids)):
        raise ValueError("pytest collection must contain unique test node ids")
    if any(not nodeid.startswith("tests/") for nodeid in nodeids):
        raise ValueError("pytest collection contains a node id outside tests/")
    return nodeids


def freeze_verification_manifest(
    *,
    commit_sha: str,
    lock_sha256: str,
    collection_command: Sequence[str],
    nodeids: Sequence[str],
    non_windows_shard_count: int,
    environment: VerificationEnvironment,
    collection_started_at: str,
    collection_ended_at: str,
    collection_exit_code: int,
    collection_stdout_sha256: str,
    collection_stderr_sha256: str,
) -> dict[str, object]:
    """Freeze non-overlapping Linux shards and the exact native-Windows runner shard."""

    _require_git_commit(commit_sha)
    _require_sha256(lock_sha256, "lock_sha256")
    _require_timestamp_order(collection_started_at, collection_ended_at)
    if collection_exit_code != 0:
        raise ValueError("frozen test collection must exit successfully")
    _require_sha256(collection_stdout_sha256, "collection_stdout_sha256")
    _require_sha256(collection_stderr_sha256, "collection_stderr_sha256")
    if non_windows_shard_count < 1 or non_windows_shard_count > 64:
        raise ValueError("non-Windows shard count must be between 1 and 64")
    ordered = tuple(sorted(nodeids))
    if not ordered or len(ordered) != len(set(ordered)):
        raise ValueError("verification inventory must contain unique node ids")
    windows = tuple(nodeid for nodeid in ordered if nodeid.startswith(f"{_WINDOWS_RUNNER_FILE}::"))
    if len(windows) != 13:
        raise ValueError("native-Windows shard must contain the exact 13 runner tests")
    windows_set = frozenset(windows)
    non_windows = tuple(nodeid for nodeid in ordered if nodeid not in windows_set)
    if non_windows_shard_count > len(non_windows):
        raise ValueError("non-Windows shard count cannot exceed assigned tests")
    buckets: list[list[str]] = [[] for _ in range(non_windows_shard_count)]
    for index, nodeid in enumerate(non_windows):
        buckets[index % non_windows_shard_count].append(nodeid)
    shards = [
        _shard_payload(
            shard_id=f"non-windows-{index + 1:02d}",
            required_os="non-Windows",
            nodeids=tuple(bucket),
        )
        for index, bucket in enumerate(buckets)
    ]
    shards.append(
        _shard_payload(
            shard_id="native-windows-powershell",
            required_os="Windows",
            nodeids=windows,
        )
    )
    payload: dict[str, object] = {
        "schema_version": _SCHEMA_VERSION,
        "commit_sha": commit_sha,
        "lock_sha256": lock_sha256,
        "collection_command": list(collection_command),
        "collection_started_at": collection_started_at,
        "collection_ended_at": collection_ended_at,
        "collection_exit_code": collection_exit_code,
        "collection_stdout_sha256": collection_stdout_sha256,
        "collection_stderr_sha256": collection_stderr_sha256,
        "collection_count": len(ordered),
        "collection_sha256": sha256_bytes(nodeids_bytes(ordered)),
        "freeze_environment": environment.to_dict(),
        "shards": shards,
    }
    payload["manifest_sha256"] = _payload_identity(payload)
    verify_frozen_manifest(payload)
    return payload


def verify_frozen_manifest(payload: Mapping[str, object]) -> None:
    """Verify one frozen inventory is complete, unique, and identity-bound."""

    expected_fields = {
        "schema_version",
        "commit_sha",
        "lock_sha256",
        "collection_command",
        "collection_started_at",
        "collection_ended_at",
        "collection_exit_code",
        "collection_stdout_sha256",
        "collection_stderr_sha256",
        "collection_count",
        "collection_sha256",
        "freeze_environment",
        "shards",
        "manifest_sha256",
    }
    if set(payload) != expected_fields or payload["schema_version"] != _SCHEMA_VERSION:
        raise ValueError("verification manifest has unexpected fields or schema")
    _require_git_commit(payload["commit_sha"])
    _require_sha256(payload["lock_sha256"], "lock_sha256")
    _require_sha256(payload["collection_sha256"], "collection_sha256")
    _require_timestamp_order(
        payload["collection_started_at"],
        payload["collection_ended_at"],
    )
    if payload["collection_exit_code"] != 0:
        raise ValueError("verification manifest collection did not exit successfully")
    _require_sha256(payload["collection_stdout_sha256"], "collection_stdout_sha256")
    _require_sha256(payload["collection_stderr_sha256"], "collection_stderr_sha256")
    _require_sha256(payload["manifest_sha256"], "manifest_sha256")
    if payload["manifest_sha256"] != _payload_identity(payload):
        raise ValueError("verification manifest identity changed")
    freeze_environment = payload["freeze_environment"]
    if not isinstance(freeze_environment, dict):
        raise ValueError("verification freeze environment is invalid")
    frozen_environment = _verification_environment_from_payload(freeze_environment)
    if payload["collection_command"] != [
        frozen_environment.executable,
        "-m",
        "pytest",
        "--collect-only",
        "-q",
    ]:
        raise ValueError("verification collection command is not reproducible")
    shards = payload["shards"]
    if not isinstance(shards, list) or not shards:
        raise ValueError("verification manifest requires shards")
    all_nodeids: list[str] = []
    shard_ids: list[str] = []
    for shard in shards:
        if not isinstance(shard, dict) or set(shard) != {
            "shard_id",
            "required_os",
            "nodeids",
            "nodeid_count",
            "nodeids_sha256",
        }:
            raise ValueError("verification shard has unexpected fields")
        shard_id = shard["shard_id"]
        required_os = shard["required_os"]
        nodeids = shard["nodeids"]
        if not isinstance(shard_id, str) or not isinstance(required_os, str):
            raise ValueError("verification shard identity is invalid")
        if not isinstance(nodeids, list) or any(not isinstance(item, str) for item in nodeids):
            raise ValueError("verification shard node ids are invalid")
        if not nodeids:
            raise ValueError("verification shards must not be empty")
        if shard["nodeid_count"] != len(nodeids):
            raise ValueError("verification shard count differs from its node ids")
        if shard["nodeids_sha256"] != sha256_bytes(nodeids_bytes(nodeids)):
            raise ValueError("verification shard node-id identity changed")
        shard_ids.append(shard_id)
        all_nodeids.extend(nodeids)
    if len(shard_ids) != len(set(shard_ids)):
        raise ValueError("verification shard ids must be unique")
    if len(all_nodeids) != len(set(all_nodeids)):
        raise ValueError("verification shards overlap")
    if payload["collection_count"] != len(all_nodeids):
        raise ValueError("verification shards do not cover the complete collection")
    if payload["collection_sha256"] != sha256_bytes(nodeids_bytes(sorted(all_nodeids))):
        raise ValueError("verification shards differ from the frozen collection")
    windows = [
        shard for shard in shards if isinstance(shard, dict) and shard["required_os"] == "Windows"
    ]
    if (
        len(windows) != 1
        or windows[0]["shard_id"] != "native-windows-powershell"
        or windows[0]["nodeid_count"] != 13
        or any(
            not nodeid.startswith(f"{_WINDOWS_RUNNER_FILE}::") for nodeid in windows[0]["nodeids"]
        )
    ):
        raise ValueError("verification manifest requires one exact 13-test Windows shard")
    non_windows_shards = [shard for shard in shards if shard["required_os"] == "non-Windows"]
    if len(non_windows_shards) != len(shards) - 1 or any(
        re.fullmatch(r"non-windows-[0-9]{2}", str(shard["shard_id"])) is None
        or any(nodeid.startswith(f"{_WINDOWS_RUNNER_FILE}::") for nodeid in shard["nodeids"])
        for shard in non_windows_shards
    ):
        raise ValueError("verification manifest has invalid non-Windows shards")


def build_shard_result(
    *,
    manifest: Mapping[str, object],
    shard_id: str,
    environment: VerificationEnvironment,
    command: Sequence[str],
    started_at: str,
    ended_at: str,
    exit_code: int,
    stdout_sha256: str,
    stderr_sha256: str,
    statuses: Mapping[str, str],
) -> dict[str, object]:
    """Build and verify one exact shard-result publication."""

    verify_frozen_manifest(manifest)
    shard = _find_shard(manifest, shard_id)
    if shard["required_os"] == "Windows" and environment.operating_system != "Windows":
        raise ValueError("native-Windows result requires a Windows environment")
    if shard["required_os"] == "non-Windows" and environment.operating_system == "Windows":
        raise ValueError("non-Windows result cannot use a Windows environment")
    expected = tuple(_require_string_list(shard["nodeids"], "shard nodeids"))
    _require_timestamp_order(started_at, ended_at)
    if not isinstance(exit_code, int):
        raise ValueError("shard exit code must be an integer")
    _require_sha256(stdout_sha256, "stdout_sha256")
    _require_sha256(stderr_sha256, "stderr_sha256")
    command_items = tuple(command)
    expected_prefix = (
        "-m",
        "pytest",
        "-q",
        "-p",
        "market_structure_lab.research.pytest_result_plugin",
    )
    if (
        len(command_items) != len(expected) + 6
        or command_items[0] != environment.executable
        or command_items[1:6] != expected_prefix
        or command_items[6:] != expected
    ):
        raise ValueError("shard command differs from the frozen pytest invocation")
    if set(statuses) != set(expected):
        raise ValueError("shard result does not account for every assigned node id exactly once")
    if any(status not in _RESULT_STATUSES for status in statuses.values()):
        raise ValueError("shard result contains an invalid status")
    ordered_results = [{"nodeid": nodeid, "status": statuses[nodeid]} for nodeid in expected]
    counts = Counter(statuses.values())
    payload: dict[str, object] = {
        "schema_version": _SCHEMA_VERSION,
        "manifest_sha256": manifest["manifest_sha256"],
        "commit_sha": manifest["commit_sha"],
        "lock_sha256": manifest["lock_sha256"],
        "collection_sha256": manifest["collection_sha256"],
        "shard_id": shard_id,
        "required_os": shard["required_os"],
        "environment": environment.to_dict(),
        "command": list(command_items),
        "started_at": started_at,
        "ended_at": ended_at,
        "exit_code": exit_code,
        "stdout_sha256": stdout_sha256,
        "stderr_sha256": stderr_sha256,
        "counts": {status: counts.get(status, 0) for status in sorted(_RESULT_STATUSES)},
        "results": ordered_results,
    }
    payload["result_sha256"] = _payload_identity(payload)
    return payload


def reconcile_shard_results(
    *,
    manifest: Mapping[str, object],
    results: Iterable[Mapping[str, object]],
    generated_at: str,
) -> dict[str, object]:
    """Reconcile every frozen test exactly once across platform-specific shards."""

    verify_frozen_manifest(manifest)
    by_shard: dict[str, Mapping[str, object]] = {}
    all_results: dict[str, str] = {}
    result_identities: list[str] = []
    nonzero_exit_shards: list[str] = []
    for result in results:
        shard_id = result.get("shard_id")
        if not isinstance(shard_id, str) or shard_id in by_shard:
            raise ValueError("verification result shard identity is missing or duplicated")
        if (
            result.get("manifest_sha256") != manifest["manifest_sha256"]
            or result.get("commit_sha") != manifest["commit_sha"]
            or result.get("lock_sha256") != manifest["lock_sha256"]
            or result.get("collection_sha256") != manifest["collection_sha256"]
        ):
            raise ValueError("verification result differs from the frozen manifest")
        expected_shard = _find_shard(manifest, shard_id)
        if result.get("required_os") != expected_shard["required_os"]:
            raise ValueError("verification result ran on the wrong platform class")
        environment = result.get("environment")
        if not isinstance(environment, dict):
            raise ValueError("verification result environment is invalid")
        operating_system = environment.get("operating_system")
        if expected_shard["required_os"] == "Windows" and operating_system != "Windows":
            raise ValueError("native-Windows result was not produced on Windows")
        if expected_shard["required_os"] == "non-Windows" and operating_system == "Windows":
            raise ValueError("non-Windows result was produced on Windows")
        exit_code = result.get("exit_code")
        if not isinstance(exit_code, int):
            raise ValueError("verification result exit code is invalid")
        if exit_code != 0:
            nonzero_exit_shards.append(shard_id)
        rows = result.get("results")
        if not isinstance(rows, list):
            raise ValueError("verification result rows are invalid")
        observed_nodeids: list[str] = []
        for row in rows:
            if not isinstance(row, dict) or set(row) != {"nodeid", "status"}:
                raise ValueError("verification result row is invalid")
            nodeid = row["nodeid"]
            status = row["status"]
            if not isinstance(nodeid, str) or status not in _RESULT_STATUSES:
                raise ValueError("verification result status is invalid")
            if nodeid in all_results:
                raise ValueError("verification result accounts for a test more than once")
            observed_nodeids.append(nodeid)
            all_results[nodeid] = str(status)
        if observed_nodeids != expected_shard["nodeids"]:
            raise ValueError("verification result differs from its exact shard inventory")
        command = _require_string_list(result.get("command"), "result command")
        environment_object = _verification_environment_from_payload(environment)
        rebuilt = build_shard_result(
            manifest=manifest,
            shard_id=shard_id,
            environment=environment_object,
            command=command,
            started_at=str(result.get("started_at")),
            ended_at=str(result.get("ended_at")),
            exit_code=exit_code,
            stdout_sha256=str(result.get("stdout_sha256")),
            stderr_sha256=str(result.get("stderr_sha256")),
            statuses={row["nodeid"]: row["status"] for row in rows},
        )
        if result != rebuilt:
            raise ValueError("verification result fields or counts differ from replay")
        result_identity = result.get("result_sha256")
        _require_sha256(result_identity, "result_sha256")
        if result_identity != _payload_identity(result):
            raise ValueError("verification result identity changed")
        result_identities.append(str(result_identity))
        by_shard[shard_id] = result
    manifest_shards = cast(list[dict[str, object]], manifest["shards"])
    expected_shard_ids = [str(shard["shard_id"]) for shard in manifest_shards]
    if set(by_shard) != set(expected_shard_ids):
        raise ValueError("verification results do not contain every frozen shard")
    if len(all_results) != manifest["collection_count"]:
        raise ValueError("verification results do not cover the complete test collection")
    counts = Counter(all_results.values())
    payload: dict[str, object] = {
        "schema_version": _SCHEMA_VERSION,
        "manifest_sha256": manifest["manifest_sha256"],
        "commit_sha": manifest["commit_sha"],
        "lock_sha256": manifest["lock_sha256"],
        "collection_sha256": manifest["collection_sha256"],
        "collection_count": manifest["collection_count"],
        "generated_at": generated_at,
        "result_sha256s": result_identities,
        "counts": {status: counts.get(status, 0) for status in sorted(_RESULT_STATUSES)},
        "all_shards_completed": True,
        "nonzero_exit_shards": nonzero_exit_shards,
        "verification_passed": counts.get("failed", 0) == 0 and not nonzero_exit_shards,
    }
    payload["reconciliation_sha256"] = _payload_identity(payload)
    verify_reconciliation(payload, manifest=manifest)
    return payload


def verify_reconciliation(payload: Mapping[str, object], *, manifest: Mapping[str, object]) -> None:
    """Verify a reconciliation publication against its frozen collection manifest."""

    verify_frozen_manifest(manifest)
    expected_fields = {
        "schema_version",
        "manifest_sha256",
        "commit_sha",
        "lock_sha256",
        "collection_sha256",
        "collection_count",
        "generated_at",
        "result_sha256s",
        "counts",
        "all_shards_completed",
        "nonzero_exit_shards",
        "verification_passed",
        "reconciliation_sha256",
    }
    if set(payload) != expected_fields or payload["schema_version"] != _SCHEMA_VERSION:
        raise ValueError("verification reconciliation has unexpected fields or schema")
    for field_name in (
        "manifest_sha256",
        "commit_sha",
        "lock_sha256",
        "collection_sha256",
        "collection_count",
    ):
        if payload[field_name] != manifest[field_name]:
            raise ValueError("verification reconciliation differs from its manifest")
    result_sha256s = _require_string_list(payload["result_sha256s"], "result_sha256s")
    if len(result_sha256s) != len(manifest["shards"]):  # type: ignore[arg-type]
        raise ValueError("verification reconciliation result count is incomplete")
    for identity in result_sha256s:
        _require_sha256(identity, "result_sha256")
    counts = payload["counts"]
    if (
        not isinstance(counts, dict)
        or set(counts) != _RESULT_STATUSES
        or any(
            isinstance(value, bool) or not isinstance(value, int) or value < 0
            for value in counts.values()
        )
        or sum(counts.values()) != payload["collection_count"]
    ):
        raise ValueError("verification reconciliation counts are invalid")
    nonzero = _require_string_list(payload["nonzero_exit_shards"], "nonzero_exit_shards")
    manifest_shard_ids = {
        str(shard["shard_id"]) for shard in cast(list[dict[str, object]], manifest["shards"])
    }
    if len(nonzero) != len(set(nonzero)) or not set(nonzero).issubset(manifest_shard_ids):
        raise ValueError("verification reconciliation exit shards are invalid")
    expected_passed = counts["failed"] == 0 and not nonzero
    if (
        payload["all_shards_completed"] is not True
        or payload["verification_passed"] is not expected_passed
    ):
        raise ValueError("verification reconciliation verdict is invalid")
    _require_timestamp(payload["generated_at"])
    _require_sha256(payload["reconciliation_sha256"], "reconciliation_sha256")
    if payload["reconciliation_sha256"] != _payload_identity(payload):
        raise ValueError("verification reconciliation identity changed")


def canonical_bytes(payload: object) -> bytes:
    """Return deterministic newline-terminated public JSON bytes."""

    return publication_json_bytes(payload)


def _shard_payload(*, shard_id: str, required_os: str, nodeids: Sequence[str]) -> dict[str, object]:
    return {
        "shard_id": shard_id,
        "required_os": required_os,
        "nodeids": list(nodeids),
        "nodeid_count": len(nodeids),
        "nodeids_sha256": sha256_bytes(nodeids_bytes(nodeids)),
    }


def _find_shard(manifest: Mapping[str, object], shard_id: str) -> dict[str, object]:
    shards = manifest.get("shards")
    if not isinstance(shards, list):
        raise ValueError("verification manifest shards are invalid")
    matches = [
        shard for shard in shards if isinstance(shard, dict) and shard.get("shard_id") == shard_id
    ]
    if len(matches) != 1:
        raise ValueError("verification shard id is not frozen exactly once")
    return matches[0]


def _payload_identity(payload: Mapping[str, object]) -> str:
    if "result_sha256s" in payload or "reconciliation_sha256" in payload:
        own_identity = "reconciliation_sha256"
    elif "shard_id" in payload or "result_sha256" in payload:
        own_identity = "result_sha256"
    else:
        own_identity = "manifest_sha256"
    stripped = {key: value for key, value in payload.items() if key != own_identity}
    return sha256_bytes(publication_json_bytes(stripped))


def _require_sha256(value: object, field_name: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise ValueError(f"{field_name} must be a lowercase SHA-256")
    return value


def _require_git_commit(value: object) -> str:
    if (
        not isinstance(value, str)
        or len(value) not in {40, 64}
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise ValueError("commit_sha must be a lowercase Git object identity")
    return value


def _require_string_list(value: object, field_name: str) -> list[str]:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise ValueError(f"{field_name} must be a list of strings")
    return value


def _require_timestamp_order(started_at: object, ended_at: object) -> None:
    if not isinstance(started_at, str) or not isinstance(ended_at, str):
        raise ValueError("verification timestamps must be UTC strings")
    try:
        start = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
        end = datetime.fromisoformat(ended_at.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError("verification timestamps are invalid") from error
    if (
        not started_at.endswith("Z")
        or not ended_at.endswith("Z")
        or start.tzinfo is None
        or end.tzinfo is None
        or end < start
    ):
        raise ValueError("verification timestamps are not ordered UTC values")


def _require_timestamp(value: object) -> None:
    _require_timestamp_order(value, value)


def _verification_environment_from_payload(
    payload: Mapping[str, object],
) -> VerificationEnvironment:
    expected = {
        "python_version",
        "python_implementation",
        "executable",
        "operating_system",
        "platform",
    }
    if set(payload) != expected or any(not isinstance(payload[key], str) for key in expected):
        raise ValueError("verification result environment fields are invalid")
    return VerificationEnvironment(
        python_version=str(payload["python_version"]),
        python_implementation=str(payload["python_implementation"]),
        executable=str(payload["executable"]),
        operating_system=str(payload["operating_system"]),
        platform=str(payload["platform"]),
    )
