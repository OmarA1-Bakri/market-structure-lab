"""Freeze, run, and reconcile deterministic Phase 5 repository-test shards."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from typing import cast, Sequence

from market_structure_lab.research.phase5_verification import (
    VerificationEnvironment,
    build_shard_result,
    canonical_bytes,
    freeze_verification_manifest,
    parse_collected_nodeids,
    reconcile_shard_results,
    utc_now,
    verify_frozen_manifest,
    verify_reconciliation,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    freeze = subparsers.add_parser("freeze")
    freeze.add_argument("--output", type=Path, required=True)
    freeze.add_argument("--shards", type=int, default=8)
    freeze.add_argument("--repository", type=Path, default=Path.cwd())

    run = subparsers.add_parser("run")
    run.add_argument("--manifest", type=Path, required=True)
    run.add_argument("--shard-id", required=True)
    run.add_argument("--output-root", type=Path, required=True)
    run.add_argument("--repository", type=Path, default=Path.cwd())

    reconcile = subparsers.add_parser("reconcile")
    reconcile.add_argument("--manifest", type=Path, required=True)
    reconcile.add_argument("--results-root", type=Path, required=True)
    reconcile.add_argument("--output", type=Path, required=True)
    return parser


def run(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "freeze":
        return _freeze(args.repository, args.output, args.shards)
    if args.command == "run":
        return _run_shard(args.repository, args.manifest, args.shard_id, args.output_root)
    return _reconcile(args.manifest, args.results_root, args.output)


def _freeze(repository: Path, output: Path, shard_count: int) -> int:
    repository = repository.resolve()
    _require_external_output(repository, output)
    _require_bound_worktree(repository)
    command = [sys.executable, "-m", "pytest", "--collect-only", "-q"]
    environment = _clean_pytest_environment()
    started_at = utc_now()
    completed = subprocess.run(
        command,
        cwd=repository,
        check=False,
        capture_output=True,
        env=environment,
    )
    ended_at = utc_now()
    stdout_path = _evidence_sibling(output, "collection.stdout.txt")
    stderr_path = _evidence_sibling(output, "collection.stderr.txt")
    _write_new(stdout_path, completed.stdout)
    _write_new(stderr_path, completed.stderr)
    if completed.returncode != 0:
        _write_freeze_failure(
            output=output,
            command=command,
            started_at=started_at,
            ended_at=ended_at,
            exit_code=completed.returncode,
            stdout_path=stdout_path,
            stderr_path=stderr_path,
            error=None,
        )
        return completed.returncode
    try:
        nodeids = parse_collected_nodeids(completed.stdout.decode("utf-8"))
        commit_sha = _git(repository, "rev-parse", "HEAD")
        lock_sha256 = _sha256_file(repository / "uv.lock")
        payload = freeze_verification_manifest(
            commit_sha=commit_sha,
            lock_sha256=lock_sha256,
            collection_command=command,
            nodeids=nodeids,
            non_windows_shard_count=shard_count,
            environment=VerificationEnvironment.current(),
            collection_started_at=started_at,
            collection_ended_at=ended_at,
            collection_exit_code=completed.returncode,
            collection_stdout_sha256=hashlib.sha256(completed.stdout).hexdigest(),
            collection_stderr_sha256=hashlib.sha256(completed.stderr).hexdigest(),
        )
    except (UnicodeDecodeError, RuntimeError, ValueError) as error:
        _write_freeze_failure(
            output=output,
            command=command,
            started_at=started_at,
            ended_at=ended_at,
            exit_code=completed.returncode,
            stdout_path=stdout_path,
            stderr_path=stderr_path,
            error=error,
        )
        raise
    _write_new(output, canonical_bytes(payload))
    return 0


def _run_shard(repository: Path, manifest_path: Path, shard_id: str, output_root: Path) -> int:
    repository = repository.resolve()
    _require_external_output(repository, output_root)
    _require_bound_worktree(repository)
    manifest = _load_json(manifest_path)
    verify_frozen_manifest(manifest)
    if _git(repository, "rev-parse", "HEAD") != manifest["commit_sha"]:
        raise RuntimeError("verification shard commit differs from the frozen manifest")
    if _sha256_file(repository / "uv.lock") != manifest["lock_sha256"]:
        raise RuntimeError("verification shard lock differs from the frozen manifest")
    shards = cast(list[dict[str, object]], manifest["shards"])
    shard = next(
        (item for item in shards if isinstance(item, dict) and item.get("shard_id") == shard_id),
        None,
    )
    if not isinstance(shard, dict):
        raise ValueError("unknown frozen verification shard")
    environment = VerificationEnvironment.current()
    required_os = shard["required_os"]
    if required_os == "Windows" and environment.operating_system != "Windows":
        raise RuntimeError("native-Windows shard must run on Windows")
    if required_os == "non-Windows" and environment.operating_system == "Windows":
        raise RuntimeError("non-Windows shard cannot run on Windows")
    output_root.mkdir(parents=True, exist_ok=True)
    stdout_path = output_root / f"{shard_id}.stdout.txt"
    stderr_path = output_root / f"{shard_id}.stderr.txt"
    result_path = output_root / f"{shard_id}.json"
    failure_path = output_root / f"{shard_id}.failure.json"
    if any(path.exists() for path in (stdout_path, stderr_path, result_path, failure_path)):
        raise FileExistsError("refusing to overwrite existing verification shard evidence")
    with tempfile.TemporaryDirectory(prefix="msl-phase5-pytest-") as temporary:
        plugin_path = Path(temporary) / "pytest-status.json"
        nodeids = cast(list[str], shard["nodeids"])
        command = [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "-p",
            "market_structure_lab.research.pytest_result_plugin",
            *nodeids,
        ]
        env = _clean_pytest_environment()
        env["MSL_PYTEST_RESULT_PATH"] = str(plugin_path)
        started_at = utc_now()
        with stdout_path.open("xb") as stdout, stderr_path.open("xb") as stderr:
            completed = subprocess.run(
                command,
                cwd=repository,
                check=False,
                stdout=stdout,
                stderr=stderr,
                env=env,
            )
        ended_at = utc_now()
        plugin_content = plugin_path.read_bytes() if plugin_path.exists() else b'{"statuses":{}}'
    statuses: object = None
    try:
        plugin = json.loads(plugin_content)
        if not isinstance(plugin, dict):
            raise RuntimeError("pytest shard status evidence is invalid")
        statuses = plugin.get("statuses")
        if not isinstance(statuses, dict) or any(
            not isinstance(nodeid, str) or not isinstance(status, str)
            for nodeid, status in statuses.items()
        ):
            raise RuntimeError("pytest shard status evidence is invalid")
        payload = build_shard_result(
            manifest=manifest,
            shard_id=shard_id,
            environment=environment,
            command=command,
            started_at=started_at,
            ended_at=ended_at,
            exit_code=completed.returncode,
            stdout_sha256=_sha256_file(stdout_path),
            stderr_sha256=_sha256_file(stderr_path),
            statuses=statuses,
        )
    except (RuntimeError, ValueError) as error:
        failure = {
            "schema_version": "phase5-repository-verification-shard-failure-v1",
            "manifest_sha256": manifest["manifest_sha256"],
            "shard_id": shard_id,
            "command": command,
            "environment": environment.to_dict(),
            "started_at": started_at,
            "ended_at": ended_at,
            "exit_code": completed.returncode,
            "stdout_sha256": _sha256_file(stdout_path),
            "stderr_sha256": _sha256_file(stderr_path),
            "assigned_nodeid_count": len(nodeids),
            "recorded_status_count": len(statuses) if isinstance(statuses, dict) else 0,
            "error_type": type(error).__name__,
            "error": str(error),
        }
        _write_new(failure_path, canonical_bytes(failure))
        raise
    _write_new(result_path, canonical_bytes(payload))
    return completed.returncode


def _reconcile(manifest_path: Path, results_root: Path, output: Path) -> int:
    manifest = _load_json(manifest_path)
    verify_frozen_manifest(manifest)
    for stream in ("stdout", "stderr"):
        expected_hash = manifest[f"collection_{stream}_sha256"]
        actual_hash = _sha256_file(_evidence_sibling(manifest_path, f"collection.{stream}.txt"))
        if expected_hash != actual_hash:
            raise ValueError(f"verification collection {stream} evidence identity changed")
    shards = cast(list[dict[str, object]], manifest["shards"])
    results: list[dict[str, object]] = []
    for shard in shards:
        shard_id = str(shard["shard_id"])
        result = _load_json(results_root / f"{shard_id}.json")
        for stream in ("stdout", "stderr"):
            expected_hash = result.get(f"{stream}_sha256")
            actual_hash = _sha256_file(results_root / f"{shard_id}.{stream}.txt")
            if expected_hash != actual_hash:
                raise ValueError(f"verification shard {stream} evidence identity changed")
        results.append(result)
    payload = reconcile_shard_results(
        manifest=manifest,
        results=results,
        generated_at=utc_now(),
    )
    verify_reconciliation(payload, manifest=manifest)
    _write_new(output, canonical_bytes(payload))
    return 0 if payload["verification_passed"] else 1


def _git(repository: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=repository,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return completed.stdout.strip()


def _require_bound_worktree(repository: Path) -> None:
    status = _git(repository, "status", "--porcelain", "--untracked-files=all")
    unexpected = []
    for line in status.splitlines():
        path = line[3:]
        if line.startswith("?? ") and path.startswith((".codacy/", ".vscode/")):
            continue
        unexpected.append(line)
    if unexpected:
        raise RuntimeError("verification requires a commit-bound worktree")


def _clean_pytest_environment() -> dict[str, str]:
    environment = os.environ.copy()
    environment["PYTEST_ADDOPTS"] = ""
    environment["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
    return environment


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _load_json(path: Path) -> dict[str, object]:
    payload = json.loads(path.read_bytes())
    if not isinstance(payload, dict):
        raise ValueError(f"JSON object required: {path}")
    return payload


def _write_new(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as handle:
        handle.write(content)


def _evidence_sibling(path: Path, suffix: str) -> Path:
    return path.with_name(f"{path.name}.{suffix}")


def _write_freeze_failure(
    *,
    output: Path,
    command: Sequence[str],
    started_at: str,
    ended_at: str,
    exit_code: int,
    stdout_path: Path,
    stderr_path: Path,
    error: Exception | None,
) -> None:
    failure: dict[str, object] = {
        "schema_version": "phase5-repository-verification-freeze-failure-v1",
        "command": list(command),
        "environment": VerificationEnvironment.current().to_dict(),
        "started_at": started_at,
        "ended_at": ended_at,
        "exit_code": exit_code,
        "stdout_sha256": _sha256_file(stdout_path),
        "stderr_sha256": _sha256_file(stderr_path),
        "error_type": type(error).__name__ if error is not None else None,
        "error": str(error) if error is not None else None,
    }
    _write_new(_evidence_sibling(output, "failure.json"), canonical_bytes(failure))


def _require_external_output(repository: Path, output: Path) -> None:
    resolved = output.resolve()
    if resolved == repository or resolved.is_relative_to(repository):
        raise ValueError("verification attempt evidence must be written outside the repository")


def main() -> None:
    raise SystemExit(run())


if __name__ == "__main__":
    main()
