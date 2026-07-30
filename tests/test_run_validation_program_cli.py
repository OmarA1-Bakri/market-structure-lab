from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import tomllib

import pytest

from market_structure_lab.core.identity import hash_json
from market_structure_lab.research import validation
from market_structure_lab.research.models import (
    EXPECTED_FAMILIES,
    VALIDATION_SLOT_ROSTER,
    ExecutionStatus,
    ScientificDecision,
    ValidationProgrammeConfig,
    ValidationWorkBudget,
)
from market_structure_lab.research.receipts import (
    verify_evaluation_receipt,
    verify_programme_receipt,
)

TASK14_CLOSEOUT = "5db2705a1cba37ee10d5eb3bd1fa470a5b628e3d"
TASK14_EVIDENCE = "3482882c864f1471ec1dd682631544d0c404c542"
TASK15_PLAN = "807ac28ac5616fb837c1ccea1e2bc47572ae3984"
TASK15_EVIDENCE = "afce8e889aa645cd9e48e90631698b87866f287f"
IMPLEMENTATION_CHECKPOINT = "e655152ab729b3e1e530f1bc044febca3509a6fd"
IMPLEMENTATION_EVIDENCE = "6da307a0d4756c61ddcabdf01f00d828afb55d7e"
IMPLEMENTATION_DOCUMENT = "d3520669352f0d85a27569edeefcfe84ff785e1f928ae0cc41edf91915c16111"


def _config(**changes: object) -> ValidationProgrammeConfig:
    values: dict[str, object] = {
        "task14_closeout_commit": TASK14_CLOSEOUT,
        "task14_evidence_commit": TASK14_EVIDENCE,
        "task15_plan_commit": TASK15_PLAN,
        "task15_evidence_commit": TASK15_EVIDENCE,
        "implementation_plan_checkpoint": IMPLEMENTATION_CHECKPOINT,
        "implementation_plan_evidence_commit": IMPLEMENTATION_EVIDENCE,
        "implementation_plan_document_sha256": IMPLEMENTATION_DOCUMENT,
        "code_commit": "1" * 40,
        "lockfile_sha256": "2" * 64,
        "dataset_sha256": "3" * 64,
        "cost_policy_sha256": "4" * 64,
        "control_policy_sha256": "5" * 64,
        "split_sha256": "6" * 64,
        "profile_config_sha256": "7" * 64,
        "source_price_precision_sha256": "8" * 64,
        "families": EXPECTED_FAMILIES,
        "roster": VALIDATION_SLOT_ROSTER,
        "work_budget": ValidationWorkBudget(),
    }
    values.update(changes)
    return ValidationProgrammeConfig(**values)  # type: ignore[arg-type]


def _write_config(path: Path, config: ValidationProgrammeConfig) -> None:
    payload = {
        **config.to_dict(),
        "config_sha256": config.sha256,
        "programme_id": config.programme_id,
    }
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _source_payload(**changes: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "schema_version": "phase5-validation-source-preflight-v1",
        "component": "development",
        "status": "missing_prerequisites",
        "final_holdout_access_count": 0,
        "eligible_symbols": [
            "ADAUSDT",
            "APTUSDT",
            "DOGEUSDT",
            "SOLUSDT",
            "XRPUSDT",
        ],
        "source_conflict_symbols": ["BTCUSDT", "ETHUSDT"],
        "mapping_incompatible_symbols": [],
        "common_complete_days": 826,
        "source_publication_path": (
            "data/exports/reconciliation/promotions/run_id=RR-000008/receipt.json"
        ),
        "source_publication_sha256": "a" * 64,
        "missing_prerequisites": [
            "aggregate_publications",
            "event_level_cost_evidence",
            "profile_price_precision_evidence",
        ],
    }
    payload.update(changes)
    payload["manifest_sha256"] = hash_json(
        "phase5-validation-source-preflight",
        {key: value for key, value in payload.items() if key != "manifest_sha256"},
    )
    return payload


def _write_source(path: Path, **changes: object) -> dict[str, object]:
    payload = _source_payload(**changes)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return payload


def test_terminal_preflight_publication_is_atomic_complete_and_non_final(tmp_path: Path) -> None:
    config = _config()
    publish = getattr(validation, "publish_validation_preflight_failure", None)

    assert publish is not None, "Task 10 requires a public metadata-only preflight publisher"
    result = publish(
        config,
        output_root=tmp_path / "receipts",
        reason_code="missing_prerequisites",
        missing_prerequisites=(
            "aggregate_publications",
            "event_level_cost_evidence",
            "profile_price_precision_evidence",
        ),
        source_manifest_sha256="9" * 64,
    )

    programme = verify_programme_receipt(result.programme_receipt_path)
    assert {
        (
            row["terminal_state"]["execution_status"],
            row["terminal_state"]["decision"],
        )
        for row in programme.ledger
    } == {(ExecutionStatus.FAILED.value, ScientificDecision.NOT_EVALUATED.value)}
    assert len(result.evaluation_receipts) == 1_104
    assert all(
        item.receipt.terminal_state["execution_status"] == ExecutionStatus.FAILED.value
        and item.receipt.terminal_state["decision"] == ScientificDecision.NOT_EVALUATED.value
        for item in result.evaluation_receipts
    )
    verify_evaluation_receipt(result.evaluation_receipts[0].path, config=config)
    assert result.final_holdout_access_count == 0


def test_task10_cli_module_exists() -> None:
    assert importlib.util.find_spec("market_structure_lab.cli.run_validation_program") is not None


def test_terminal_preflight_publisher_is_a_narrow_research_export() -> None:
    import market_structure_lab.research as research

    assert research.publish_validation_preflight_failure is (
        validation.publish_validation_preflight_failure
    )


def test_programme_config_loader_requires_exact_frozen_identity(tmp_path: Path) -> None:
    from market_structure_lab.cli.run_validation_program import load_programme_config

    config = _config()
    path = tmp_path / "vp.json"
    _write_config(path, config)

    assert load_programme_config(path) == config

    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["config_sha256"] = "f" * 64
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="config_sha256"):
        load_programme_config(path)


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"component": "final"}, "development"),
        ({"final_holdout_access_count": 1}, "final_holdout_access_count"),
        (
            {"eligible_symbols": ["ADAUSDT", "APTUSDT", "BTCUSDT", "DOGEUSDT", "SOLUSDT"]},
            "BTCUSDT",
        ),
        ({"common_complete_days": 729}, "730"),
    ],
)
def test_source_preflight_rejects_non_development_or_ineligible_inputs(
    tmp_path: Path,
    changes: dict[str, object],
    message: str,
) -> None:
    from market_structure_lab.cli.run_validation_program import load_source_preflight

    path = tmp_path / "sources.json"
    _write_source(path, **changes)

    with pytest.raises(ValueError, match=message):
        load_source_preflight(path)


def test_cli_has_no_final_holdout_convenience_flag() -> None:
    from market_structure_lab.cli.run_validation_program import build_parser

    with pytest.raises(SystemExit):
        build_parser().parse_args(
            [
                "--config",
                "vp.json",
                "--source-preflight",
                "sources.json",
                "--output-root",
                "receipts",
                "--repo-root",
                ".",
                "--open-final-holdout",
            ]
        )


def test_repository_state_rejects_dirty_tracked_code(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from market_structure_lab.cli import run_validation_program as cli

    monkeypatch.setattr(
        cli,
        "_git_text",
        lambda _root, *args: (
            " M src/market_structure_lab/example.py"
            if args[:2] == ("status", "--porcelain=v1")
            else ""
        ),
    )

    with pytest.raises(ValueError, match="dirty"):
        cli.verify_repository_state(tmp_path, _config())


def test_project_registers_validation_cli() -> None:
    project = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))["project"]

    assert (
        project["scripts"]["msl-run-validation-program"]
        == "market_structure_lab.cli.run_validation_program:main"
    )


def test_cli_publishes_truthful_terminal_preflight(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from market_structure_lab.cli import run_validation_program as cli

    source_path = tmp_path / "sources.json"
    source = _write_source(source_path)
    config = _config(dataset_sha256=source["manifest_sha256"])
    config_path = tmp_path / "vp.json"
    _write_config(config_path, config)
    monkeypatch.setattr(cli, "verify_repository_state", lambda _root, _config: None)
    monkeypatch.setattr(cli, "verify_source_publication", lambda _root, _source: None)

    exit_code = cli.main(
        [
            "--config",
            str(config_path),
            "--source-preflight",
            str(source_path),
            "--output-root",
            str(tmp_path / "receipts"),
            "--repo-root",
            str(tmp_path),
        ]
    )

    summary = json.loads(capsys.readouterr().out)
    assert exit_code == 2
    assert summary == {
        "config_sha256": config.sha256,
        "decision": "not_evaluated",
        "evaluation_receipt_count": 1_104,
        "execution_status": "failed",
        "final_holdout_access_count": 0,
        "missing_prerequisites": source["missing_prerequisites"],
        "programme_id": config.programme_id,
        "programme_receipt_path": str(tmp_path / "receipts" / config.programme_id),
        "reason_code": "missing_prerequisites",
        "source_manifest_sha256": source["manifest_sha256"],
    }


def test_cli_redacts_credentials_and_sql(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from market_structure_lab.cli import run_validation_program as cli

    monkeypatch.setattr(
        cli,
        "_execute",
        lambda _args: (_ for _ in ()).throw(
            RuntimeError("postgresql://user:secret@host/db SELECT * FROM private_table")
        ),
    )

    exit_code = cli.main(
        [
            "--config",
            "vp.json",
            "--source-preflight",
            "sources.json",
            "--output-root",
            "receipts",
            "--repo-root",
            ".",
        ]
    )

    error = capsys.readouterr().err
    assert exit_code == 1
    assert "secret" not in error
    assert "SELECT" not in error
    assert "validation operation failed" in error
