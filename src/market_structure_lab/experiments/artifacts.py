"""Deterministic experiment artifact persistence."""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

_SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")


@dataclass(frozen=True)
class ExperimentConfig:
    run_id: str
    name: str
    question: str
    hypothesis: str
    parameters: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ExperimentResult:
    path: Path


def save_experiment_result(
    *,
    config: ExperimentConfig,
    metrics: dict[str, Any],
    summary: str,
    plots: dict[str, str] | None = None,
    artifacts: dict[str, str] | None = None,
    root: str | Path = "experiments",
) -> ExperimentResult:
    """Persist a reproducible experiment bundle."""
    _validate_safe_name("run_id", config.run_id)

    output_dir = Path(root) / config.run_id
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "plots").mkdir(exist_ok=True)
    (output_dir / "artifacts").mkdir(exist_ok=True)

    _write_json(output_dir / "config.json", asdict(config))
    _write_json(output_dir / "metrics.json", metrics)
    (output_dir / "summary.md").write_text(f"# {config.name}\n\n{summary.rstrip()}\n")

    for file_name, content in (plots or {}).items():
        _write_named_file(output_dir / "plots", file_name, content)

    for file_name, content in (artifacts or {}).items():
        _write_named_file(output_dir / "artifacts", file_name, content)

    return ExperimentResult(path=output_dir)


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")


def _write_named_file(directory: Path, file_name: str, content: str) -> None:
    _validate_safe_name("file_name", file_name)
    (directory / file_name).write_text(content)


def _validate_safe_name(field_name: str, value: str) -> None:
    if not _SAFE_NAME.fullmatch(value):
        raise ValueError(f"{field_name} must be a safe file name")
