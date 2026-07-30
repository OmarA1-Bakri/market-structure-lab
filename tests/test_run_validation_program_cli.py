from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import tomllib
from types import SimpleNamespace

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
        "common_complete_days": 825,
        "common_complete_start": "2024-04-11T12:00:00Z",
        "common_complete_end": "2026-07-16T11:23:00Z",
        "source_publication_path": (
            "data/exports/reconciliation/promotions/run_id=RR-000008/receipt.json"
        ),
        "source_publication_sha256": (
            "cb399ac01d0beeea4666da071d9186e9a144f3f7fe5e4d1bf760696c631e8529"
        ),
        "compatibility_manifest_path": (
            "data/exports/manifests/recovery-callscore-20260714-validated.json"
        ),
        "compatibility_manifest_sha256": (
            "482f3a09a4e24a62b0ad92f5bb90028eead67fe961eb1d3320dd38d13fd105d2"
        ),
        "bindings_sha256": "c" * 64,
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


def _bindings_payload() -> dict[str, object]:
    payload: dict[str, object] = {
        "schema_version": "phase5-validation-bindings-v1",
        "component": "development",
        "final_holdout_access_count": 0,
        "source_universe": {
            "promotion_receipt_path": (
                "data/exports/reconciliation/promotions/run_id=RR-000008/receipt.json"
            ),
            "promotion_receipt_sha256": (
                "cb399ac01d0beeea4666da071d9186e9a144f3f7fe5e4d1bf760696c631e8529"
            ),
            "compatibility_manifest_path": (
                "data/exports/manifests/recovery-callscore-20260714-validated.json"
            ),
            "compatibility_manifest_sha256": (
                "482f3a09a4e24a62b0ad92f5bb90028eead67fe961eb1d3320dd38d13fd105d2"
            ),
            "eligible_symbols": [
                "ADAUSDT",
                "APTUSDT",
                "DOGEUSDT",
                "SOLUSDT",
                "XRPUSDT",
            ],
            "excluded_source_conflicts": ["BTCUSDT", "ETHUSDT"],
            "common_complete_start": "2024-04-11T12:00:00Z",
            "common_complete_end": "2026-07-16T11:23:00Z",
            "common_complete_days_floor": 825,
            "status": "coverage_metadata_sufficient",
        },
        "aggregate_config": {"status": "unavailable_no_verified_publication"},
        "cost_policy": {"status": "unavailable_no_event_level_cost_publication"},
        "control_policy": {"status": "frozen_implementation_not_executed"},
        "split_policy": {"status": "blocked_before_fold_freeze"},
        "profile_config": {"status": "unavailable_without_source_price_precision_authority"},
        "source_price_precision": {"status": "unavailable_no_independently_bound_publication"},
    }
    domains = {
        "cost_policy_sha256": (
            "phase5-validation-cost-policy-declaration",
            "cost_policy",
        ),
        "control_policy_sha256": (
            "phase5-validation-control-policy-declaration",
            "control_policy",
        ),
        "split_sha256": ("phase5-validation-split-declaration", "split_policy"),
        "profile_config_sha256": (
            "phase5-validation-profile-config-declaration",
            "profile_config",
        ),
        "source_price_precision_sha256": (
            "phase5-validation-source-price-precision-declaration",
            "source_price_precision",
        ),
    }
    payload["binding_hashes"] = {
        field: hash_json(domain, payload[key]) for field, (domain, key) in domains.items()
    }
    payload["bindings_sha256"] = hash_json("phase5-validation-bindings", payload)
    return payload


def _write_bindings(path: Path) -> dict[str, object]:
    payload = _bindings_payload()
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return payload


def _write_and_return_path(path: Path, payload: dict[str, object]) -> Path:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def test_terminal_preflight_publication_is_atomic_complete_and_non_final(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    config = _config()
    publish = getattr(validation, "publish_validation_preflight_failure", None)
    verification_count = 0
    original_verify = validation.verify_evaluation_receipt

    def counting_verify(*args: object, **kwargs: object):
        nonlocal verification_count
        verification_count += 1
        return original_verify(*args, **kwargs)

    monkeypatch.setattr(validation, "verify_evaluation_receipt", counting_verify)

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
    evidence = (result.programme_receipt_path / "programme-evidence.json").read_text(
        encoding="utf-8"
    )
    assert all(item in evidence for item in result.missing_prerequisites)
    assert verification_count == 2 * 1_104


def test_terminal_preflight_rejects_symlinked_output_parent_before_publication(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    link = tmp_path / "linked-parent"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError as error:
        pytest.skip(f"symlink creation unavailable: {error}")
    monkeypatch.setattr(
        validation,
        "publish_evaluation_receipt_batch",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("publication began before parent validation")
        ),
    )

    with pytest.raises(RuntimeError, match="symlink|reparse"):
        validation.publish_validation_preflight_failure(
            _config(),
            output_root=link / "receipts",
            reason_code="missing_prerequisites",
            missing_prerequisites=("aggregate_publications",),
            source_manifest_sha256="9" * 64,
        )
    assert not (outside / "receipts").exists()


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
                "--bindings",
                "bindings.json",
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


def test_preregistration_files_must_be_repository_relative_tracked_and_remote_identical(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from market_structure_lab.cli import run_validation_program as cli

    with pytest.raises(ValueError, match="repository-relative"):
        cli.verify_preregistration_files(
            tmp_path,
            "f" * 40,
            (tmp_path / "vp.json",),
        )

    path = tmp_path / "vp.json"
    path.write_text("local", encoding="utf-8")
    monkeypatch.setattr(cli, "_git_check", lambda _root, *args: False)
    with pytest.raises(ValueError, match="tracked"):
        cli.verify_preregistration_files(tmp_path, "f" * 40, (Path("vp.json"),))

    monkeypatch.setattr(cli, "_git_check", lambda _root, *args: True)
    monkeypatch.setattr(cli, "_git_bytes", lambda _root, *args, **kwargs: b"remote")
    with pytest.raises(ValueError, match="remote"):
        cli.verify_preregistration_files(tmp_path, "f" * 40, (Path("vp.json"),))


def test_bindings_loader_recomputes_every_policy_identity(tmp_path: Path) -> None:
    from market_structure_lab.cli.run_validation_program import load_validation_bindings

    path = tmp_path / "bindings.json"
    expected = _write_bindings(path)
    bindings = load_validation_bindings(path)

    assert bindings.binding_hashes == expected["binding_hashes"]
    assert bindings.bindings_sha256 == expected["bindings_sha256"]

    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["cost_policy"]["status"] = "available"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="binding"):
        load_validation_bindings(path)


def test_source_preflight_is_recomputed_from_verified_publications(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from market_structure_lab.cli import run_validation_program as cli
    from market_structure_lab.data.reconciliation.repository import VerifiedCoverageInterval

    path = tmp_path / "sources.json"
    payload = _write_source(
        path,
        eligible_symbols=["ADAUSDT", "ALGOUSDT", "BNBUSDT", "DOGEUSDT", "DOTUSDT"],
        source_conflict_symbols=["BTCUSDT", "ETHUSDT"],
        mapping_incompatible_symbols=["BTCUSDT", "ETHUSDT"],
        common_complete_days=800,
        common_complete_start="2024-01-01T00:00:00Z",
        common_complete_end="2026-03-11T00:00:00Z",
    )
    source = cli.load_source_preflight(path)
    start = 1_704_067_200_000
    end = start + 800 * 86_400_000
    coverage = tuple(
        VerifiedCoverageInterval(symbol=symbol, timeframe="1m", start_ms=start, end_ms=end)
        for symbol in payload["eligible_symbols"]
    )
    monkeypatch.setattr(
        cli,
        "_read_verified_promotion_receipt",
        lambda _root, _source: SimpleNamespace(coverage=coverage),
    )
    monkeypatch.setattr(
        cli,
        "_read_compatibility_states",
        lambda _root, _source: {
            **{symbol: "compatible" for symbol in payload["eligible_symbols"]},
            "BTCUSDT": "source_conflict",
            "ETHUSDT": "source_conflict",
        },
    )

    cli.verify_source_publications(tmp_path, source)

    forged_path = tmp_path / "forged.json"
    forged = cli.load_source_preflight(
        _write_and_return_path(
            forged_path,
            _source_payload(
                eligible_symbols=[
                    "ADAUSDT",
                    "ALGOUSDT",
                    "BNBUSDT",
                    "DOGEUSDT",
                    "XRPUSDT",
                ],
                source_conflict_symbols=["BTCUSDT", "ETHUSDT"],
                mapping_incompatible_symbols=["BTCUSDT", "ETHUSDT"],
                common_complete_days=800,
                common_complete_start="2024-01-01T00:00:00Z",
                common_complete_end="2026-03-11T00:00:00Z",
            ),
        )
    )
    with pytest.raises(ValueError, match="eligible_symbols"):
        cli.verify_source_publications(tmp_path, forged)


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

    bindings_path = tmp_path / "bindings.json"
    bindings = _write_bindings(bindings_path)
    source_path = tmp_path / "sources.json"
    source = _write_source(source_path, bindings_sha256=bindings["bindings_sha256"])
    binding_hashes = bindings["binding_hashes"]
    assert isinstance(binding_hashes, dict)
    config = _config(
        dataset_sha256=source["manifest_sha256"],
        **binding_hashes,
    )
    config_path = tmp_path / "vp.json"
    _write_config(config_path, config)
    monkeypatch.setattr(cli, "verify_repository_state", lambda _root, _config: "f" * 40)
    monkeypatch.setattr(cli, "verify_preregistration_files", lambda *_args: None)
    monkeypatch.setattr(cli, "verify_source_publications", lambda _root, _source: None)

    exit_code = cli.main(
        [
            "--config",
            config_path.name,
            "--source-preflight",
            source_path.name,
            "--bindings",
            bindings_path.name,
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
            "--bindings",
            "bindings.json",
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
