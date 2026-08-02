from __future__ import annotations

import json
from pathlib import Path
import subprocess
import hashlib

import pytest

from market_structure_lab.cli import verify_phase5_shards as cli


def test_verification_outputs_must_be_outside_repository(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()

    with pytest.raises(ValueError, match="outside the repository"):
        cli._require_external_output(repository, repository / "evidence")  # noqa: SLF001

    cli._require_external_output(repository, tmp_path / "evidence")  # noqa: SLF001


def test_clean_pytest_environment_scrubs_ambient_options(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PYTEST_ADDOPTS", "--lf")
    monkeypatch.setenv("PYTEST_DISABLE_PLUGIN_AUTOLOAD", "0")

    environment = cli._clean_pytest_environment()  # noqa: SLF001

    assert environment["PYTEST_ADDOPTS"] == ""
    assert environment["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] == "1"


def test_write_new_refuses_to_overwrite_evidence(tmp_path: Path) -> None:
    target = tmp_path / "evidence.json"
    cli._write_new(target, b"first")  # noqa: SLF001

    with pytest.raises(FileExistsError):
        cli._write_new(target, b"second")  # noqa: SLF001

    assert target.read_bytes() == b"first"


def test_dirty_worktree_is_rejected(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(cli, "_git", lambda *_args: " M src/changed.py")

    with pytest.raises(RuntimeError, match="commit-bound"):
        cli._require_bound_worktree(tmp_path)  # noqa: SLF001


@pytest.mark.parametrize(
    ("returncode", "stdout", "expected_error_type"),
    [
        (2, b"collection failed\n", None),
        (0, b"no test node ids\n", "ValueError"),
    ],
)
def test_freeze_preserves_logs_and_publishes_failure_receipt(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    returncode: int,
    stdout: bytes,
    expected_error_type: str | None,
) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    output = tmp_path / "attempt" / "manifest.json"
    monkeypatch.setattr(cli, "_require_bound_worktree", lambda _repository: None)
    monkeypatch.setattr(
        cli.subprocess,
        "run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess(
            args=[], returncode=returncode, stdout=stdout, stderr=b"diagnostic\n"
        ),
    )

    if expected_error_type is None:
        assert cli._freeze(repository, output, 8) == returncode  # noqa: SLF001
    else:
        with pytest.raises(ValueError, match="unique"):
            cli._freeze(repository, output, 8)  # noqa: SLF001

    assert output.with_name("manifest.json.collection.stdout.txt").read_bytes() == stdout
    assert output.with_name("manifest.json.collection.stderr.txt").read_bytes() == b"diagnostic\n"
    failure = json.loads(output.with_name("manifest.json.failure.json").read_bytes())
    assert failure["exit_code"] == returncode
    assert failure["error_type"] == expected_error_type


def test_freeze_captures_environment_once_before_collection(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    output = tmp_path / "attempt" / "manifest.json"
    expected = cli.VerificationEnvironment(
        python_version="3.13.13",
        python_implementation="CPython",
        executable="python",
        operating_system="Linux",
        platform="system=Linux;release=test;version=build;machine=x86_64",
    )
    calls = 0

    def current() -> cli.VerificationEnvironment:
        nonlocal calls
        calls += 1
        if calls > 1:
            raise AssertionError("verification environment was captured more than once")
        return expected

    monkeypatch.setattr(cli, "_require_bound_worktree", lambda _repository: None)
    monkeypatch.setattr(cli.VerificationEnvironment, "current", current)
    monkeypatch.setattr(
        cli.subprocess,
        "run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess(
            args=[], returncode=2, stdout=b"collection failed\n", stderr=b"diagnostic\n"
        ),
    )

    assert cli._freeze(repository, output, 8) == 2  # noqa: SLF001

    failure = json.loads(output.with_name("manifest.json.failure.json").read_bytes())
    assert calls == 1
    assert failure["environment"] == expected.to_dict()


def test_reconcile_rehashes_collection_logs(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "collection_stdout_sha256": hashlib.sha256(b"original").hexdigest(),
                "collection_stderr_sha256": hashlib.sha256(b"").hexdigest(),
                "shards": [],
            }
        )
    )
    manifest_path.with_name("manifest.json.collection.stdout.txt").write_bytes(b"mutated")
    manifest_path.with_name("manifest.json.collection.stderr.txt").write_bytes(b"")
    monkeypatch.setattr(cli, "verify_frozen_manifest", lambda _payload: None)

    with pytest.raises(ValueError, match="collection stdout evidence identity changed"):
        cli._reconcile(  # noqa: SLF001
            manifest_path,
            tmp_path / "results",
            tmp_path / "reconciliation.json",
        )
