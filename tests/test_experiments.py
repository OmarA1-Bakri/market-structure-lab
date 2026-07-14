from __future__ import annotations

import json

import pytest

from src.experiments import ExperimentConfig, save_experiment_result


def test_save_experiment_result_writes_reproducible_artifact_bundle(tmp_path) -> None:
    config = ExperimentConfig(
        run_id="value-migration-001",
        name="Value migration baseline",
        question="Does accepted value migrate after upside imbalance?",
        hypothesis="Upside imbalance shifts next-session value higher.",
        parameters={"symbol": "BTCUSDT", "timeframe": "1m"},
    )

    result = save_experiment_result(
        config=config,
        metrics={"observations": 1250, "transition_probability": 0.62},
        summary="Initial deterministic value-migration baseline.",
        plots={"value_area.html": "<html>plot</html>"},
        artifacts={"transitions.json": '{"above_value": 12}'},
        root=tmp_path,
    )

    assert result.path == tmp_path / "value-migration-001"
    assert json.loads((result.path / "config.json").read_text()) == {
        "run_id": "value-migration-001",
        "name": "Value migration baseline",
        "question": "Does accepted value migrate after upside imbalance?",
        "hypothesis": "Upside imbalance shifts next-session value higher.",
        "parameters": {"symbol": "BTCUSDT", "timeframe": "1m"},
    }
    assert json.loads((result.path / "metrics.json").read_text()) == {
        "observations": 1250,
        "transition_probability": 0.62,
    }
    assert (result.path / "summary.md").read_text() == (
        "# Value migration baseline\n\n"
        "Initial deterministic value-migration baseline.\n"
    )
    assert (result.path / "plots" / "value_area.html").read_text() == "<html>plot</html>"
    assert (result.path / "artifacts" / "transitions.json").read_text() == '{"above_value": 12}'


@pytest.mark.parametrize(
    "unsafe_run_id",
    [
        "../escape",
        "nested/../escape",
        "..\\escape",
        "/tmp/escape",
    ],
)
def test_save_experiment_result_rejects_path_traversal(tmp_path, unsafe_run_id: str) -> None:
    config = ExperimentConfig(
        run_id=unsafe_run_id,
        name="Bad run",
        question="Can run IDs escape the experiment root?",
        hypothesis="No.",
    )

    try:
        save_experiment_result(config=config, metrics={}, summary="", root=tmp_path)
    except ValueError as error:
        assert "run_id" in str(error)
    else:
        raise AssertionError("Expected unsafe run_id to be rejected")


def test_save_experiment_result_writes_byte_for_byte_identical_bundles(tmp_path) -> None:
    config = ExperimentConfig(
        run_id="value-migration-001",
        name="Value migration baseline",
        question="Does accepted value migrate after upside imbalance?",
        hypothesis="Upside imbalance shifts next-session value higher.",
        parameters={"symbol": "BTCUSDT", "timeframe": "1m"},
    )

    first_root = tmp_path / "first"
    second_root = tmp_path / "second"

    first_result = save_experiment_result(
        config=config,
        metrics={"observations": 1250, "transition_probability": 0.62},
        summary="Initial deterministic value-migration baseline.",
        plots={"value_area.html": "<html>plot</html>"},
        artifacts={"transitions.json": '{"above_value": 12}'},
        root=first_root,
    )
    second_result = save_experiment_result(
        config=config,
        metrics={"observations": 1250, "transition_probability": 0.62},
        summary="Initial deterministic value-migration baseline.",
        plots={"value_area.html": "<html>plot</html>"},
        artifacts={"transitions.json": '{"above_value": 12}'},
        root=second_root,
    )

    first_files = sorted(str(path.relative_to(first_result.path)) for path in first_result.path.rglob("*"))
    second_files = sorted(str(path.relative_to(second_result.path)) for path in second_result.path.rglob("*"))

    assert first_files == second_files

    for first_path, second_path in zip(
        sorted(first_result.path.rglob("*")),
        sorted(second_result.path.rglob("*")),
    ):
        if first_path.is_file() and second_path.is_file():
            assert first_path.read_bytes() == second_path.read_bytes()
