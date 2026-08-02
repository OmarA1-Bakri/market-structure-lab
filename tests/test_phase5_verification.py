from __future__ import annotations

from copy import deepcopy
import platform
import sys
from typing import cast

import pytest

from market_structure_lab.research.phase5_verification import (
    VerificationEnvironment,
    build_shard_result,
    freeze_verification_manifest,
    parse_collected_nodeids,
    reconcile_shard_results,
    verify_frozen_manifest,
    verify_reconciliation,
)

_SHA = "a" * 40
_LOCK = "b" * 64
_WINDOWS = tuple(
    f"tests/test_daily_candle_refresh_runner.py::test_runner_{index}" for index in range(13)
)
_OTHER = tuple(f"tests/test_example.py::test_case_{index}" for index in range(9))
_ENVIRONMENT = VerificationEnvironment(
    python_version="3.13.13",
    python_implementation="CPython",
    executable="/trusted/python",
    operating_system="Linux",
    platform="Linux-test",
)


def _manifest() -> dict[str, object]:
    return freeze_verification_manifest(
        commit_sha=_SHA,
        lock_sha256=_LOCK,
        collection_command=[_ENVIRONMENT.executable, "-m", "pytest", "--collect-only", "-q"],
        nodeids=(*_OTHER, *_WINDOWS),
        non_windows_shard_count=3,
        environment=_ENVIRONMENT,
        collection_started_at="2026-08-03T00:00:00.000000Z",
        collection_ended_at="2026-08-03T00:00:10.000000Z",
        collection_exit_code=0,
        collection_stdout_sha256="c" * 64,
        collection_stderr_sha256="d" * 64,
    )


def test_collection_parser_requires_unique_test_nodeids() -> None:
    assert parse_collected_nodeids("tests/test_a.py::test_one\n\n1 test collected in 0.01s\n") == (
        "tests/test_a.py::test_one",
    )

    with pytest.raises(ValueError, match="unique"):
        parse_collected_nodeids("tests/test_a.py::test_one\ntests/test_a.py::test_one\n")


def test_current_environment_binds_the_exact_running_executable() -> None:
    assert VerificationEnvironment.current().executable == sys.executable


def test_current_environment_binds_explicit_os_build_components(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(platform, "system", lambda: "Windows")
    monkeypatch.setattr(platform, "release", lambda: "11")
    monkeypatch.setattr(platform, "version", lambda: "10.0.26200")
    monkeypatch.setattr(platform, "machine", lambda: "AMD64")

    environment = VerificationEnvironment.current()

    assert environment.operating_system == "Windows"
    assert environment.platform == "system=Windows;release=11;version=10.0.26200;machine=AMD64"


def test_manifest_freezes_complete_non_overlapping_cross_platform_shards() -> None:
    manifest = _manifest()
    verify_frozen_manifest(manifest)

    shards = manifest["shards"]
    assert isinstance(shards, list)
    assert [shard["nodeid_count"] for shard in shards[:-1]] == [3, 3, 3]
    assert shards[-1]["shard_id"] == "native-windows-powershell"
    assert shards[-1]["nodeids"] == sorted(_WINDOWS)
    assert manifest == _manifest()


def test_manifest_rejects_overlap_and_identity_mutation() -> None:
    overlap = deepcopy(_manifest())
    shards = overlap["shards"]
    assert isinstance(shards, list)
    shards[1]["nodeids"][0] = shards[0]["nodeids"][0]
    with pytest.raises(ValueError, match="identity changed|overlap"):
        verify_frozen_manifest(overlap)


def test_manifest_rejects_empty_non_windows_shards() -> None:
    with pytest.raises(ValueError, match="cannot exceed assigned tests"):
        freeze_verification_manifest(
            commit_sha=_SHA,
            lock_sha256=_LOCK,
            collection_command=[
                _ENVIRONMENT.executable,
                "-m",
                "pytest",
                "--collect-only",
                "-q",
            ],
            nodeids=(*_OTHER, *_WINDOWS),
            non_windows_shard_count=len(_OTHER) + 1,
            environment=_ENVIRONMENT,
            collection_started_at="2026-08-03T00:00:00.000000Z",
            collection_ended_at="2026-08-03T00:00:10.000000Z",
            collection_exit_code=0,
            collection_stdout_sha256="c" * 64,
            collection_stderr_sha256="d" * 64,
        )


def test_reconciliation_accounts_for_every_test_exactly_once() -> None:
    manifest = _manifest()
    results: list[dict[str, object]] = []
    shards = manifest["shards"]
    assert isinstance(shards, list)
    for shard in shards:
        environment = (
            _ENVIRONMENT
            if shard["required_os"] == "non-Windows"
            else VerificationEnvironment(
                python_version="3.13.13",
                python_implementation="CPython",
                executable="D:/trusted/python.exe",
                operating_system="Windows",
                platform="Windows-test",
            )
        )
        statuses = {nodeid: "passed" for nodeid in shard["nodeids"]}
        results.append(
            build_shard_result(
                manifest=manifest,
                shard_id=shard["shard_id"],
                environment=environment,
                command=[
                    environment.executable,
                    "-m",
                    "pytest",
                    "-q",
                    "-p",
                    "market_structure_lab.research.pytest_result_plugin",
                    *shard["nodeids"],
                ],
                started_at="2026-08-03T00:00:00.000000Z",
                ended_at="2026-08-03T00:01:00.000000Z",
                exit_code=0,
                stdout_sha256="c" * 64,
                stderr_sha256="d" * 64,
                statuses=statuses,
            )
        )

    reconciliation = reconcile_shard_results(
        manifest=manifest,
        results=results,
        generated_at="2026-08-03T00:02:00.000000Z",
    )

    assert reconciliation["collection_count"] == len(_OTHER) + len(_WINDOWS)
    counts = cast(dict[str, int], reconciliation["counts"])
    assert counts["passed"] == len(_OTHER) + len(_WINDOWS)
    assert reconciliation["verification_passed"] is True
    verify_reconciliation(reconciliation, manifest=manifest)


def test_reconciliation_rejects_missing_shard() -> None:
    manifest = _manifest()
    with pytest.raises(ValueError, match="every frozen shard"):
        reconcile_shard_results(
            manifest=manifest,
            results=[],
            generated_at="2026-08-03T00:02:00.000000Z",
        )


def test_windows_result_requires_native_windows_environment() -> None:
    manifest = _manifest()
    with pytest.raises(ValueError, match="Windows environment"):
        build_shard_result(
            manifest=manifest,
            shard_id="native-windows-powershell",
            environment=_ENVIRONMENT,
            command=[
                _ENVIRONMENT.executable,
                "-m",
                "pytest",
                "-q",
                "-p",
                "market_structure_lab.research.pytest_result_plugin",
                *sorted(_WINDOWS),
            ],
            started_at="2026-08-03T00:00:00.000000Z",
            ended_at="2026-08-03T00:01:00.000000Z",
            exit_code=0,
            stdout_sha256="c" * 64,
            stderr_sha256="d" * 64,
            statuses={nodeid: "passed" for nodeid in sorted(_WINDOWS)},
        )


def test_manifest_rejects_self_consistent_wrong_platform_shards() -> None:
    manifest = _manifest()
    shards = manifest["shards"]
    assert isinstance(shards, list)
    windows = shards[-1]
    windows["nodeids"] = [f"tests/test_other.py::test_bad_{index}" for index in range(13)]
    windows["nodeid_count"] = 13
    from market_structure_lab.research import phase5_verification as module

    windows["nodeids_sha256"] = module.sha256_bytes(module.nodeids_bytes(windows["nodeids"]))
    manifest["collection_sha256"] = module.sha256_bytes(
        module.nodeids_bytes(sorted(nodeid for shard in shards for nodeid in shard["nodeids"]))
    )
    manifest["manifest_sha256"] = module._payload_identity(manifest)  # noqa: SLF001

    with pytest.raises(ValueError, match="Windows shard"):
        verify_frozen_manifest(manifest)


def test_reconciliation_identity_tamper_is_rejected() -> None:
    manifest = _manifest()
    results = []
    shards = manifest["shards"]
    assert isinstance(shards, list)
    for shard in shards:
        environment = _ENVIRONMENT
        if shard["required_os"] == "Windows":
            environment = VerificationEnvironment(
                "3.13.13", "CPython", "D:/python.exe", "Windows", "Windows-test"
            )
        results.append(
            build_shard_result(
                manifest=manifest,
                shard_id=shard["shard_id"],
                environment=environment,
                command=[
                    environment.executable,
                    "-m",
                    "pytest",
                    "-q",
                    "-p",
                    "market_structure_lab.research.pytest_result_plugin",
                    *shard["nodeids"],
                ],
                started_at="2026-08-03T00:00:00.000000Z",
                ended_at="2026-08-03T00:01:00.000000Z",
                exit_code=0,
                stdout_sha256="c" * 64,
                stderr_sha256="d" * 64,
                statuses={nodeid: "passed" for nodeid in shard["nodeids"]},
            )
        )
    reconciliation = reconcile_shard_results(
        manifest=manifest,
        results=results,
        generated_at="2026-08-03T00:02:00.000000Z",
    )
    counts = cast(dict[str, int], reconciliation["counts"])
    counts["failed"] = 1
    with pytest.raises(ValueError, match="counts|identity"):
        verify_reconciliation(reconciliation, manifest=manifest)
