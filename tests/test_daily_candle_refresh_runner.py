from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import pytest


PROJECT_RUNNER = Path("scripts/run_daily_candle_refresh.ps1")
REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
COMPATIBILITY_SHA = "4" * 64
MANIFEST_SHA = "a" * 64
REPORT_SHA = "b" * 64
CONFLICTS = ("BTCUSDT", "ETHUSDT")


def _fake_program() -> str:
    return r"""
import json
import os
import sys
import time
from pathlib import Path

mode, *args = sys.argv[1:]
trace_path = Path(os.environ["FAKE_TRACE"])
scenario_path = Path(os.environ["FAKE_SCENARIO"])
project = Path(os.environ["FAKE_PROJECT"])
scenario = json.loads(scenario_path.read_text(encoding="utf-8"))
with trace_path.open("a", encoding="utf-8") as stream:
    stream.write(json.dumps({"mode": mode, "args": args}) + "\n")

if mode == "docker":
    if args[0] == "info":
        if scenario.get("docker_info_exit", 0):
            print("daemon unavailable", file=sys.stderr)
            raise SystemExit(scenario["docker_info_exit"])
        print("27.0")
        raise SystemExit(0)
    if args[0] == "inspect":
        if (project / "docker_started").exists():
            print("running|healthy")
            raise SystemExit(0)
        state = scenario.get("container_state", "running|healthy")
        if state is None:
            print("No such container", file=sys.stderr)
            raise SystemExit(1)
        print(state)
        raise SystemExit(0)
    if args[:2] == ["compose", "start"] or args[:2] == ["compose", "up"]:
        (project / "docker_started").write_text("started", encoding="utf-8")
        raise SystemExit(scenario.get("compose_exit", 0))
    raise SystemExit(9)

operation = args[0]
if operation == "plan":
    if scenario.get("plan_sleep_seconds"):
        time.sleep(float(scenario["plan_sleep_seconds"]))
    manifest = project / "data" / "exports" / "freshness" / "frozen.plan.json"
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(
        json.dumps({"manifest": {"as_of": "2026-07-16T00:00:00Z"}, "sha256": "a" * 64}),
        encoding="utf-8",
    )
    print(json.dumps({
        "manifest": str(manifest),
        "manifest_sha256": "a" * 64,
        "symbols": 3,
        "missing_minutes": 2,
        "eligible_minutes": 0,
    }))
    raise SystemExit(scenario.get("plan_exit", 2))

if operation == "run" and "--apply" not in args:
    manifest = args[args.index("--manifest") + 1]
    print(json.dumps({
        "dry_run": True,
        "manifest_sha256": "a" * 64,
        "manifest_seen": manifest,
        "symbols": 3,
        "missing_minutes": 2,
        "eligible_minutes": 0,
    }))
    raise SystemExit(scenario.get("dry_run_exit", 2))

if operation == "run" and "--apply" in args:
    if scenario.get("apply_exit", 2) not in (0, 2):
        print("network failure", file=sys.stderr)
        raise SystemExit(scenario["apply_exit"])
    statuses = scenario.get("statuses", {})
    conflicts = set(scenario.get("expected_conflicts", ["BTCUSDT", "ETHUSDT"]))
    symbols = []
    for symbol in ("BTCUSDT", "ETHUSDT", "XRPUSDT"):
        default = "source_conflict" if symbol in conflicts else "up_to_date"
        status = statuses.get(symbol, default)
        conflict = symbol in conflicts
        current = status in ("up_to_date", "recovered")
        symbols.append({
            "symbol": symbol,
            "compatibility_state": "source_conflict" if conflict else "compatible",
            "status": status,
            "inserted_rows": 0,
            "current_through_cutoff": current,
            "after_missing_minutes": 0 if current else 1,
        })
    report_sha = scenario.get("apply_report_sha", "b" * 64)
    print(json.dumps({
        "manifest_sha256": "a" * 64,
        "report_sha256": report_sha,
        "coverage_conserved": scenario.get("coverage_conserved", True),
        "symbols": symbols,
    }))
    raise SystemExit(scenario.get("apply_exit", 2))

if operation == "health":
    print(json.dumps({
        "current": scenario.get("health_current", False),
        "report_sha256": scenario.get("health_report_sha", "b" * 64),
        "symbols_current": 1,
        "symbols_total": 3,
        "missing_minutes": 2,
    }))
    raise SystemExit(scenario.get("health_exit", 2))

raise SystemExit(8)
"""


def _write_cmd(path: Path, program: Path, mode: str) -> None:
    command = f'"{sys.executable}" "{program}" {mode} %*'
    if os.name != "nt":
        command = f'python.exe "{_pwsh_path(program)}" {mode} %*'
    path.write_text(
        f"@echo off\r\n{command}\r\nexit /b %ERRORLEVEL%\r\n",
        encoding="utf-8",
    )


def _pwsh_path(path: Path) -> str:
    if os.name == "nt":
        return str(path)
    return subprocess.run(
        ["wslpath", "-w", str(path.resolve())],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


def _workspace(tmp_path: Path, scenario: dict[str, Any] | None = None) -> dict[str, Path]:
    scenario = scenario or {}
    root = tmp_path / "workspace"
    runner = root / PROJECT_RUNNER
    runner.parent.mkdir(parents=True)
    shutil.copy2(PROJECT_RUNNER, runner)
    compatibility = root / "data/exports/manifests/compatibility.json"
    compatibility.parent.mkdir(parents=True)
    compatibility.write_text(
        json.dumps(
            {
                "provenance_validation": {
                    symbol: (
                        "source_conflict"
                        if symbol in set(scenario.get("expected_conflicts", ["BTCUSDT", "ETHUSDT"]))
                        else "compatible"
                    )
                    for symbol in ("BTCUSDT", "ETHUSDT", "XRPUSDT")
                }
            }
        ),
        encoding="utf-8",
    )
    postgres = root / "data/postgres"
    postgres.mkdir(parents=True)
    (postgres / "PG_VERSION").write_text("17\n", encoding="utf-8")
    dump = root / "data/dumps/callscore.dump"
    dump.parent.mkdir(parents=True)
    dump.write_bytes(b"immutable test dump")
    program = root / "fake_program.py"
    program.write_text(_fake_program(), encoding="utf-8")
    docker = root / "fake-docker.cmd"
    sync = root / "fake-sync.cmd"
    _write_cmd(docker, program, "docker")
    _write_cmd(sync, program, "sync")
    trace = root / "trace.jsonl"
    scenario_path = root / "scenario.json"
    scenario_path.write_text(json.dumps(scenario), encoding="utf-8")
    return {
        "root": root,
        "runner": runner,
        "compatibility": compatibility,
        "postgres": postgres,
        "docker": docker,
        "sync": sync,
        "trace": trace,
        "scenario": scenario_path,
    }


def _run(
    workspace: dict[str, Path],
    *,
    extra: tuple[str, ...] = (),
    timeout: int = 120,
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env.update(
        {
            "FAKE_PROJECT": _pwsh_path(workspace["root"]),
            "FAKE_SCENARIO": _pwsh_path(workspace["scenario"]),
            "FAKE_TRACE": _pwsh_path(workspace["trace"]),
        }
    )
    if os.name != "nt":
        env["WSLENV"] = ":".join(
            filter(
                None,
                (env.get("WSLENV"), "FAKE_PROJECT", "FAKE_SCENARIO", "FAKE_TRACE"),
            )
        )
    return subprocess.run(
        [
            "pwsh.exe",
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            _pwsh_path(workspace["runner"]),
            "-ProjectRoot",
            _pwsh_path(workspace["root"]),
            "-CompatibilityPath",
            _pwsh_path(workspace["compatibility"]),
            "-CompatibilitySha256",
            COMPATIBILITY_SHA,
            "-DockerExecutable",
            _pwsh_path(workspace["docker"]),
            "-SyncExecutable",
            _pwsh_path(workspace["sync"]),
            "-ContainerHealthTimeoutSeconds",
            "2",
            "-PollIntervalSeconds",
            "0",
            *extra,
        ],
        cwd=REPOSITORY_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def _trace(workspace: dict[str, Path]) -> list[dict[str, Any]]:
    if not workspace["trace"].exists():
        return []
    return [
        json.loads(line)
        for line in workspace["trace"].read_text(encoding="utf-8").splitlines()
        if line
    ]


def _sync_calls(workspace: dict[str, Path]) -> list[list[str]]:
    return [entry["args"] for entry in _trace(workspace) if entry["mode"] == "sync"]


def test_runner_reuses_exact_manifest_and_classifies_reviewed_conflicts(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)

    result = _run(workspace)

    assert result.returncode == 2, result.stderr
    calls = _sync_calls(workspace)
    assert [call[0] for call in calls] == ["plan", "run", "run", "health"]
    dry_manifest = calls[1][calls[1].index("--manifest") + 1]
    apply_manifest = calls[2][calls[2].index("--manifest") + 1]
    assert dry_manifest == apply_manifest
    assert "--apply" not in calls[1]
    assert "--apply" in calls[2]
    assert not any(call[0] in {"bootstrap", "snapshot"} for call in calls)
    latest = json.loads(
        (workspace["root"] / "data/exports/freshness/automation/latest.json").read_text(
            encoding="utf-8"
        )
    )
    assert latest["classification"] == "expected_provenance_conflicts"
    assert latest["expected_conflicts"] == list(CONFLICTS)
    assert latest["report_sha256"] == REPORT_SHA
    assert not (workspace["root"] / "data/exports/freshness/automation/pending.json").exists()


def test_runner_returns_operational_failure_for_unexpected_compatible_status(
    tmp_path: Path,
) -> None:
    workspace = _workspace(tmp_path, {"statuses": {"XRPUSDT": "fetch_failed"}})

    result = _run(workspace)

    assert result.returncode == 1
    assert "compatible symbol XRPUSDT" in result.stderr
    assert (workspace["root"] / "data/exports/freshness/automation/pending.json").exists()


@pytest.mark.parametrize("status", ["provider_absent", "non_trading", "partially_recovered"])
def test_runner_advances_after_terminal_compatible_coverage_limit(
    tmp_path: Path,
    status: str,
) -> None:
    workspace = _workspace(tmp_path, {"statuses": {"XRPUSDT": status}})

    result = _run(workspace)

    assert result.returncode == 2, result.stderr
    latest = json.loads(
        (workspace["root"] / "data/exports/freshness/automation/latest.json").read_text(
            encoding="utf-8"
        )
    )
    assert latest["classification"] == "terminal_data_limits"
    assert latest["terminal_limits"] == [f"XRPUSDT:{status}"]
    assert not (workspace["root"] / "data/exports/freshness/automation/pending.json").exists()


def test_runner_returns_zero_when_reviewed_universe_is_fully_current(tmp_path: Path) -> None:
    workspace = _workspace(
        tmp_path,
        {
            "expected_conflicts": [],
            "plan_exit": 0,
            "dry_run_exit": 0,
            "apply_exit": 0,
            "health_exit": 0,
            "health_current": True,
        },
    )

    result = _run(workspace)

    assert result.returncode == 0, result.stderr
    latest = json.loads(
        (workspace["root"] / "data/exports/freshness/automation/latest.json").read_text(
            encoding="utf-8"
        )
    )
    assert latest["classification"] == "current"
    assert latest["expected_conflicts"] == []


def test_runner_preserves_pending_manifest_and_resumes_without_new_plan(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path, {"apply_exit": 1})
    first = _run(workspace)
    assert first.returncode == 1
    pending = workspace["root"] / "data/exports/freshness/automation/pending.json"
    assert pending.exists()
    pending_manifest = json.loads(pending.read_text(encoding="utf-8"))["manifest"]
    first_calls = _sync_calls(workspace)
    workspace["scenario"].write_text(json.dumps({}), encoding="utf-8")

    second = _run(workspace)

    assert second.returncode == 2, second.stderr
    all_calls = _sync_calls(workspace)
    resumed_calls = all_calls[len(first_calls) :]
    assert [call[0] for call in resumed_calls] == ["run", "run", "health"]
    assert resumed_calls[0][resumed_calls[0].index("--manifest") + 1] == pending_manifest
    assert not pending.exists()


def test_runner_rejects_report_hash_mismatch_and_preserves_pending(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path, {"health_report_sha": "c" * 64})

    result = _run(workspace)

    assert result.returncode == 1
    assert "report checksums differ" in result.stderr
    assert (workspace["root"] / "data/exports/freshness/automation/pending.json").exists()


def test_runner_refuses_to_start_postgres_without_durable_version_marker(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    (workspace["postgres"] / "PG_VERSION").unlink()

    result = _run(workspace)

    assert result.returncode == 1
    assert "PG_VERSION is absent" in result.stderr
    assert _trace(workspace) == []


@pytest.mark.parametrize(
    ("container_state", "expected_command"),
    [
        ("exited|none", ["compose", "start", "postgres"]),
        (None, ["compose", "up", "-d", "--no-deps", "postgres"]),
    ],
)
def test_runner_starts_only_existing_durable_postgres_safely(
    tmp_path: Path,
    container_state: str | None,
    expected_command: list[str],
) -> None:
    workspace = _workspace(tmp_path, {"container_state": container_state})

    result = _run(workspace)

    assert result.returncode == 1 or result.returncode == 2
    docker_calls = [entry["args"] for entry in _trace(workspace) if entry["mode"] == "docker"]
    assert expected_command in docker_calls
    flattened = " ".join(part for call in docker_calls for part in call)
    assert "down" not in flattened
    assert " -v " not in f" {flattened} "


def test_runner_fails_cleanly_when_docker_daemon_is_unavailable(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path, {"docker_info_exit": 1})

    result = _run(workspace)

    assert result.returncode == 1
    assert "docker info failed" in result.stderr
    assert not _sync_calls(workspace)


def test_runner_host_lock_rejects_a_concurrent_invocation(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path, {"plan_sleep_seconds": 3})
    env = os.environ.copy()
    env.update(
        {
            "FAKE_PROJECT": _pwsh_path(workspace["root"]),
            "FAKE_SCENARIO": _pwsh_path(workspace["scenario"]),
            "FAKE_TRACE": _pwsh_path(workspace["trace"]),
        }
    )
    if os.name != "nt":
        env["WSLENV"] = ":".join(
            filter(
                None,
                (env.get("WSLENV"), "FAKE_PROJECT", "FAKE_SCENARIO", "FAKE_TRACE"),
            )
        )
    command = [
        "pwsh.exe",
        "-NoLogo",
        "-NoProfile",
        "-NonInteractive",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        _pwsh_path(workspace["runner"]),
        "-ProjectRoot",
        _pwsh_path(workspace["root"]),
        "-CompatibilityPath",
        _pwsh_path(workspace["compatibility"]),
        "-CompatibilitySha256",
        COMPATIBILITY_SHA,
        "-DockerExecutable",
        _pwsh_path(workspace["docker"]),
        "-SyncExecutable",
        _pwsh_path(workspace["sync"]),
        "-PollIntervalSeconds",
        "0",
    ]
    first = subprocess.Popen(
        command,
        cwd=REPOSITORY_ROOT,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    lock = workspace["root"] / "data/exports/freshness/automation/daily-refresh.lock"
    deadline = time.monotonic() + 30
    while not lock.exists() and time.monotonic() < deadline:
        time.sleep(0.05)
    assert lock.exists()

    second = subprocess.run(
        command,
        cwd=REPOSITORY_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    first_stdout, first_stderr = first.communicate(timeout=120)

    assert first.returncode == 2, first_stderr
    assert second.returncode == 1
    assert "used by another process" in second.stderr.lower()
    assert first_stdout
