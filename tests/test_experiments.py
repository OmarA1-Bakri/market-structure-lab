from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest

from market_structure_lab.experiments import (
    ArtifactIdentity,
    ExperimentConfig,
    ExperimentMode,
    TerminalStatus,
    TrialAbandoned,
    TrialOutput,
    TrialRange,
    execute_trial_attempt,
    read_experiment_result,
    read_trial_ledger,
    save_experiment_result,
    verify_trial_receipt,
)


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _config(
    *,
    run_id: str = "TR-000001",
    mode: ExperimentMode = ExperimentMode.HYPOTHESIS,
) -> ExperimentConfig:
    return ExperimentConfig(
        run_id=run_id,
        mode=mode,
        dataset_snapshot=ArtifactIdentity("DS-000001", _sha("dataset")),
        feature_publication=ArtifactIdentity("FP-000001", _sha("features")),
        feature_registry=ArtifactIdentity("FR-000001", _sha("registry")),
        normalizer=ArtifactIdentity("NZ-000001", _sha("normalizer")),
        frozen_split={"split_id": "split-v1", "sha256": _sha("split")},
        detector_version="detector-v1",
        candidate_id="HC-000001" if mode is not ExperimentMode.DISCOVERY else None,
        candidate_version="candidate-v1" if mode is not ExperimentMode.DISCOVERY else None,
        code_commit="abcdef1",
        lock_sha256=_sha("lock"),
        canonical_config={"clusters": 2, "tolerance": 1e-6},
        seed=7,
        symbols=("BTCUSDT",),
        timeframes=("1m",),
        ranges=(
            TrialRange(
                symbol="BTCUSDT",
                timeframe="1m",
                start="2025-01-01T00:00:00Z",
                end="2025-02-01T00:00:00Z",
            ),
        ),
        parent_ids=(),
        metrics_schema={"observations": "integer", "score": "number"},
        hypothesis=(
            None
            if mode is ExperimentMode.DISCOVERY
            else "The frozen detector recurs on untouched development data."
        ),
        outcome_policy=(
            ArtifactIdentity("OP-000001", _sha("outcomes"))
            if mode in {ExperimentMode.VALIDATION, ExperimentMode.STRATEGY}
            else None
        ),
        cost_policy=(
            ArtifactIdentity("CP-000001", _sha("costs"))
            if mode in {ExperimentMode.VALIDATION, ExperimentMode.STRATEGY}
            else None
        ),
    )


STARTED = datetime(2026, 7, 17, 3, 0, tzinfo=UTC)
COMPLETED = datetime(2026, 7, 17, 3, 1, tzinfo=UTC)


def _save(tmp_path, *, config: ExperimentConfig | None = None, **changes):
    values = {
        "config": config or _config(),
        "status": TerminalStatus.COMPLETED,
        "metrics": {"observations": 1250, "score": 0.62},
        "conclusion": "Frozen detector completed without outcome access.",
        "started_at": STARTED,
        "completed_at": COMPLETED,
        "artifacts": {"artifacts/transitions.json": '{"above_value":12}\n'},
        "root": tmp_path,
    }
    values.update(changes)
    return save_experiment_result(**values)


def _symlink(target: Path, link: Path, *, directory: bool = False) -> None:
    try:
        link.symlink_to(target, target_is_directory=directory)
    except OSError as error:
        pytest.skip(f"symlinks are unavailable: {error}")


def _rewrite_receipt_for_metrics(result, metrics_bytes: bytes) -> None:
    metrics_path = result.path / "metrics.json"
    metrics_path.write_bytes(metrics_bytes)
    receipt_path = result.path / "receipt.json"
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    receipt["artifact_sha256"]["metrics.json"] = hashlib.sha256(metrics_bytes).hexdigest()
    receipt.pop("receipt_sha256")
    receipt["receipt_sha256"] = hashlib.sha256(
        json.dumps(receipt, allow_nan=False, separators=(",", ":"), sort_keys=True).encode()
    ).hexdigest()
    receipt_path.write_bytes(
        json.dumps(receipt, allow_nan=False, separators=(",", ":"), sort_keys=True).encode() + b"\n"
    )


def test_identical_trial_replay_is_byte_idempotent(tmp_path) -> None:
    first = _save(tmp_path)
    before = {
        path.relative_to(first.path): path.read_bytes()
        for path in first.path.rglob("*")
        if path.is_file()
    }

    replay = _save(tmp_path)

    assert replay.manifest == first.manifest
    assert before == {
        path.relative_to(first.path): path.read_bytes()
        for path in first.path.rglob("*")
        if path.is_file()
    }
    assert first.manifest.schema_version == "trial-receipt-v2"
    assert first.manifest.status is TerminalStatus.COMPLETED
    assert verify_trial_receipt(first.path) == first.manifest


def test_conflicting_run_id_reuse_fails_without_overwriting_bytes(tmp_path) -> None:
    first = _save(tmp_path)
    before = {path: path.read_bytes() for path in first.path.rglob("*") if path.is_file()}

    with pytest.raises(RuntimeError, match="identity conflict"):
        _save(tmp_path, config=replace(_config(), seed=11))

    assert {path: path.read_bytes() for path in first.path.rglob("*") if path.is_file()} == before


@pytest.mark.parametrize("unsafe", ["../escape", "nested/escape", "..\\escape", ".hidden"])
def test_trial_run_id_cannot_escape_or_create_nested_paths(unsafe: str) -> None:
    with pytest.raises(ValueError, match="run_id"):
        replace(_config(), run_id=unsafe)


def test_trial_artifact_paths_are_safe_and_receipt_name_is_reserved(tmp_path) -> None:
    with pytest.raises(ValueError, match="artifact path"):
        _save(tmp_path, artifacts={"../escape.json": "{}"})
    with pytest.raises(ValueError, match="reserved"):
        _save(tmp_path, artifacts={"receipt.json": "{}"})


@pytest.mark.parametrize("mutation", ["stale", "extra"])
def test_trial_verification_rejects_stale_or_extra_files(tmp_path, mutation: str) -> None:
    result = _save(tmp_path)
    path = result.path / ("metrics.json" if mutation == "stale" else "artifacts/unmanifested.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{}\n", encoding="utf-8")

    with pytest.raises(RuntimeError, match="tamper|unexpected"):
        verify_trial_receipt(result.path)


def test_trial_verification_rejects_symlinked_artifact_without_reading_outside_root(
    tmp_path,
) -> None:
    result = _save(tmp_path / "ledger")
    metrics_path = result.path / "metrics.json"
    outside = tmp_path / "outside-metrics.json"
    outside.write_bytes(metrics_path.read_bytes())
    metrics_path.unlink()
    _symlink(outside, metrics_path)

    with pytest.raises(RuntimeError, match="symlink|regular"):
        verify_trial_receipt(result.path)


def test_trial_verification_rejects_symlinked_directory_entry(tmp_path) -> None:
    result = _save(tmp_path / "ledger")
    outside = tmp_path / "outside-directory"
    outside.mkdir()
    _symlink(outside, result.path / "linked-directory", directory=True)

    with pytest.raises(RuntimeError, match="symlink|regular"):
        verify_trial_receipt(result.path)


def test_trial_ledger_rejects_symlinked_receipt_directory(tmp_path) -> None:
    outside_ledger = tmp_path / "outside"
    result = _save(outside_ledger)
    ledger = tmp_path / "ledger"
    ledger.mkdir()
    _symlink(result.path, ledger / result.path.name, directory=True)

    with pytest.raises(RuntimeError, match="symlink|regular"):
        read_trial_ledger(ledger)


def test_trial_publication_rejects_symlinked_ledger_root_before_writes(tmp_path) -> None:
    outside = tmp_path / "outside-ledger"
    outside.mkdir()
    linked_root = tmp_path / "linked-ledger"
    _symlink(outside, linked_root, directory=True)

    with pytest.raises(RuntimeError, match="symlink|regular"):
        _save(linked_root)

    assert tuple(outside.iterdir()) == ()


@pytest.mark.parametrize(
    ("entry_name", "message"),
    [
        (".TR-000001.staging", "staging"),
        ("TR-000001", "symlink|regular"),
    ],
    ids=["dangling-stage", "dangling-final"],
)
def test_trial_publication_rejects_dangling_links_before_writes(
    tmp_path, entry_name: str, message: str
) -> None:
    ledger = tmp_path / "ledger"
    ledger.mkdir()
    _symlink(tmp_path / "missing-target", ledger / entry_name, directory=True)

    with pytest.raises(RuntimeError, match=message):
        _save(ledger)

    assert {child.name for child in ledger.iterdir()} == {entry_name}


def test_metrics_json_is_mandatory_even_for_terminal_failure(tmp_path) -> None:
    result = _save(tmp_path, status=TerminalStatus.FAILED, metrics={})
    (result.path / "metrics.json").unlink()

    with pytest.raises(RuntimeError, match="missing|unexpected"):
        verify_trial_receipt(result.path)


def test_metrics_json_forgery_is_detected(tmp_path) -> None:
    result = _save(tmp_path)
    (result.path / "metrics.json").write_text(
        '{"observations":1250,"score":0.61}\n', encoding="utf-8"
    )

    with pytest.raises(RuntimeError, match="tamper"):
        verify_trial_receipt(result.path)


@pytest.mark.parametrize(
    "metrics_bytes",
    [
        b'{"observations":"1250","score":0.62}\n',
        b'{"observations":true,"score":0.62}\n',
    ],
    ids=["wrong-type", "bool-is-not-integer"],
)
def test_receipt_verification_enforces_metrics_schema_types(tmp_path, metrics_bytes: bytes) -> None:
    result = _save(tmp_path)
    _rewrite_receipt_for_metrics(result, metrics_bytes)

    with pytest.raises(RuntimeError, match="metrics"):
        verify_trial_receipt(result.path)


def test_receipt_verification_rejects_noncanonical_metrics_json(tmp_path) -> None:
    result = _save(tmp_path)
    _rewrite_receipt_for_metrics(
        result,
        b'{\n  "score": 0.62,\n  "observations": 1250\n}\n',
    )

    with pytest.raises(RuntimeError, match="metrics.*canonical"):
        verify_trial_receipt(result.path)


def test_save_rejects_boolean_for_integer_metric(tmp_path) -> None:
    with pytest.raises(ValueError, match="observations.*integer"):
        _save(tmp_path, metrics={"observations": True, "score": 0.62})


def test_metrics_schema_rejects_unknown_kind() -> None:
    with pytest.raises(ValueError, match="metrics_schema.*kind"):
        replace(_config(), metrics_schema={"score": "float"})


def test_interrupted_sibling_stage_cannot_publish(tmp_path) -> None:
    stage = tmp_path / ".TR-000001.staging"
    stage.mkdir()
    (stage / "partial.json").write_text("{}\n", encoding="utf-8")

    with pytest.raises(RuntimeError, match="stale trial staging"):
        _save(tmp_path)

    assert not (tmp_path / "TR-000001").exists()
    assert (stage / "partial.json").read_text(encoding="utf-8") == "{}\n"


@pytest.mark.parametrize("status", list(TerminalStatus))
def test_every_canonical_trial_receipt_has_an_immutable_terminal_status(
    tmp_path,
    status: TerminalStatus,
) -> None:
    result = _save(
        tmp_path,
        config=replace(_config(), run_id=f"TR-{list(TerminalStatus).index(status) + 1:06d}"),
        status=status,
    )

    assert verify_trial_receipt(result.path).status is status


def test_attempt_boundary_persists_failure_then_reraises_original_exception(tmp_path) -> None:
    error = LookupError("sensitive upstream detail must not be persisted")

    def algorithm() -> TrialOutput:
        raise error

    with pytest.raises(LookupError) as raised:
        execute_trial_attempt(
            config=_config(),
            algorithm=algorithm,
            root=tmp_path,
            started_at=STARTED,
            completed_at=lambda: COMPLETED,
        )

    assert raised.value is error
    receipt = verify_trial_receipt(tmp_path / "TR-000001")
    assert receipt.status is TerminalStatus.FAILED
    assert receipt.conclusion == "Trial execution raised LookupError."
    assert "sensitive upstream detail" not in (tmp_path / "TR-000001" / "receipt.json").read_text()


def test_attempt_boundary_persists_abandonment_then_reraises_original_exception(tmp_path) -> None:
    error = TrialAbandoned("operator stop detail")

    def algorithm() -> TrialOutput:
        raise error

    with pytest.raises(TrialAbandoned) as raised:
        execute_trial_attempt(
            config=_config(),
            algorithm=algorithm,
            root=tmp_path,
            started_at=STARTED,
            completed_at=lambda: COMPLETED,
        )

    assert raised.value is error
    receipt = verify_trial_receipt(tmp_path / "TR-000001")
    assert receipt.status is TerminalStatus.ABANDONED


@pytest.mark.parametrize(
    "algorithm",
    [
        lambda: object(),
        lambda: TrialOutput(  # type: ignore[arg-type]
            status="not-terminal",
            metrics={"observations": 1, "score": 0.5},
            conclusion="Invalid status.",
        ),
        lambda: TrialOutput(
            status=TerminalStatus.COMPLETED,
            metrics={"unexpected": 1},
            conclusion="Invalid metrics.",
        ),
        lambda: TrialOutput(
            status=TerminalStatus.COMPLETED,
            metrics={"observations": 1, "score": 0.5},
            conclusion="Invalid artifact.",
            artifacts={"receipt.json": "reserved"},
        ),
    ],
    ids=["wrong-output-type", "invalid-status", "invalid-metrics", "invalid-artifact"],
)
def test_attempt_boundary_classifies_output_validation_or_publication_failure(
    tmp_path,
    algorithm,
) -> None:
    with pytest.raises((TypeError, ValueError)):
        execute_trial_attempt(
            config=_config(),
            algorithm=algorithm,
            root=tmp_path,
            started_at=STARTED,
            completed_at=lambda: COMPLETED,
        )

    receipt = verify_trial_receipt(tmp_path / "TR-000001")
    assert receipt.status is TerminalStatus.FAILED
    assert receipt.conclusion.startswith("Trial execution raised ")


def test_attempt_boundary_never_replaces_existing_receipt_when_publication_conflicts(
    tmp_path,
) -> None:
    published = _save(tmp_path)
    before = {path: path.read_bytes() for path in published.path.rglob("*") if path.is_file()}

    with pytest.raises(RuntimeError, match="content conflict") as raised:
        execute_trial_attempt(
            config=_config(),
            algorithm=lambda: TrialOutput(
                status=TerminalStatus.COMPLETED,
                metrics={"observations": 999, "score": 0.1},
                conclusion="Conflicting replay.",
            ),
            root=tmp_path,
            started_at=STARTED,
            completed_at=lambda: COMPLETED,
        )

    assert "receipt publication also failed" in " ".join(raised.value.__notes__)
    assert verify_trial_receipt(published.path).status is TerminalStatus.COMPLETED
    assert {
        path: path.read_bytes() for path in published.path.rglob("*") if path.is_file()
    } == before


def test_attempt_preflight_rejects_stale_stage_before_algorithm_invocation(tmp_path) -> None:
    (tmp_path / ".TR-000001.staging").mkdir()
    called = False

    def algorithm() -> TrialOutput:
        nonlocal called
        called = True
        return TrialOutput(TerminalStatus.COMPLETED, {}, "Must not run.")

    with pytest.raises(RuntimeError, match="staging"):
        execute_trial_attempt(
            config=_config(),
            algorithm=algorithm,
            root=tmp_path,
            started_at=STARTED,
            completed_at=lambda: COMPLETED,
        )

    assert called is False


def test_attempt_preflight_rejects_conflicting_receipt_before_algorithm_invocation(
    tmp_path,
) -> None:
    _save(tmp_path)
    called = False

    def algorithm() -> TrialOutput:
        nonlocal called
        called = True
        return TrialOutput(TerminalStatus.COMPLETED, {}, "Must not run.")

    with pytest.raises(RuntimeError, match="identity conflict"):
        execute_trial_attempt(
            config=replace(_config(), seed=11),
            algorithm=algorithm,
            root=tmp_path,
            started_at=STARTED,
            completed_at=lambda: COMPLETED,
        )

    assert called is False


def test_validation_and_strategy_require_frozen_outcome_and_cost_policies() -> None:
    for mode in (ExperimentMode.VALIDATION, ExperimentMode.STRATEGY):
        with pytest.raises(ValueError, match="outcome_policy"):
            replace(_config(mode=mode), outcome_policy=None)
        with pytest.raises(ValueError, match="cost_policy"):
            replace(_config(mode=mode), cost_policy=None)


def test_discovery_is_hypothesis_optional_but_other_modes_require_candidate_linkage() -> None:
    assert _config(mode=ExperimentMode.DISCOVERY).hypothesis is None
    with pytest.raises(ValueError, match="candidate"):
        replace(_config(), candidate_id=None)
    with pytest.raises(ValueError, match="hypothesis"):
        replace(_config(), hypothesis=None)


def test_reader_counts_only_verified_canonical_receipts(tmp_path) -> None:
    _save(tmp_path, config=_config(run_id="TR-000001", mode=ExperimentMode.DISCOVERY))
    _save(
        tmp_path,
        config=_config(run_id="TR-000002", mode=ExperimentMode.VALIDATION),
        status=TerminalStatus.REJECTED,
    )

    receipts = read_trial_ledger(tmp_path)

    assert [(item.run_id, item.mode, item.status) for item in receipts] == [
        ("TR-000001", ExperimentMode.DISCOVERY, TerminalStatus.COMPLETED),
        ("TR-000002", ExperimentMode.VALIDATION, TerminalStatus.REJECTED),
    ]


def test_receipt_verifier_accepts_only_exact_final_or_exact_staging_directory_name(
    tmp_path,
) -> None:
    result = _save(tmp_path)
    disguised = tmp_path / ".TR-000001.staging-evil"
    result.path.rename(disguised)

    with pytest.raises(RuntimeError, match="path identity"):
        verify_trial_receipt(disguised)


@pytest.mark.parametrize("entry", [".metadata", ".TR-000002.staging-evil", "TR-000002.staging-old"])
def test_ledger_reader_rejects_every_hidden_or_staging_like_entry(tmp_path, entry: str) -> None:
    _save(tmp_path)
    (tmp_path / entry).mkdir()

    with pytest.raises(RuntimeError, match="hidden|staging"):
        read_trial_ledger(tmp_path)


@pytest.mark.parametrize("run_id", ["staging", "TR-staging-001", "TR.001.staging"])
def test_run_id_reserves_staging_path_token(run_id: str) -> None:
    with pytest.raises(ValueError, match="run_id.*staging"):
        replace(_config(), run_id=run_id)


def test_non_token_staging_substring_remains_a_readable_run_id(tmp_path) -> None:
    result = _save(tmp_path, config=replace(_config(), run_id="backstaging-001"))

    assert read_trial_ledger(tmp_path) == (result.manifest,)


def test_legacy_v1_bundle_remains_readable_without_invented_provenance(tmp_path) -> None:
    legacy = tmp_path / "legacy-001"
    legacy.mkdir()
    (legacy / "config.json").write_text('{"run_id":"legacy-001"}\n', encoding="utf-8")
    config_sha = hashlib.sha256((legacy / "config.json").read_bytes()).hexdigest()
    (legacy / "manifest.json").write_text(
        json.dumps(
            {
                "schema_version": "experiment-manifest-v1",
                "run_id": "legacy-001",
                "mode": "hypothesis",
                "artifact_sha256": {"config.json": config_sha},
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )

    record = read_experiment_result(legacy)

    assert record.legacy is True
    assert record.schema_version == "experiment-manifest-v1"
    assert record.dataset_snapshot is None


def test_legacy_v1_reader_rejects_symlinked_artifact(tmp_path) -> None:
    legacy = tmp_path / "legacy-001"
    legacy.mkdir()
    config_path = legacy / "config.json"
    config_path.write_text('{"run_id":"legacy-001"}\n', encoding="utf-8")
    config_sha = hashlib.sha256(config_path.read_bytes()).hexdigest()
    (legacy / "manifest.json").write_text(
        json.dumps(
            {
                "schema_version": "experiment-manifest-v1",
                "run_id": "legacy-001",
                "mode": "hypothesis",
                "artifact_sha256": {"config.json": config_sha},
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    outside = tmp_path / "outside-config.json"
    outside.write_bytes(config_path.read_bytes())
    config_path.unlink()
    _symlink(outside, config_path)

    with pytest.raises(RuntimeError, match="symlink|regular"):
        read_experiment_result(legacy)
