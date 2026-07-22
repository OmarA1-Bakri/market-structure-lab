from __future__ import annotations

import json
from pathlib import Path

import pytest

import market_structure_lab.cli.run_discovery_program as discovery_cli


@pytest.mark.parametrize(
    ("run_status", "reliability_conclusion", "expected_exit"),
    [
        ("completed", "accepted", 0),
        ("rejected", "rejected", 0),
        ("failed", "inconclusive", 1),
        ("inconclusive", "inconclusive", 1),
        ("completed", "inconclusive", 1),
        ("completed", "rejected", 1),
        ("rejected", "accepted", 1),
        ("unknown", "accepted", 1),
        (None, "accepted", 1),
        ("completed", None, 1),
    ],
)
def test_main_returns_nonzero_for_failed_or_inconclusive_terminal_evidence(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    run_status: str | None,
    reliability_conclusion: str | None,
    expected_exit: int,
) -> None:
    summary = {
        "run_id": "DR-000701",
        "run_status": run_status,
        "reliability_conclusion": reliability_conclusion,
    }
    monkeypatch.setattr(discovery_cli, "_execute", lambda _args: summary)

    exit_code = discovery_cli.main([])

    assert exit_code == expected_exit
    assert json.loads(capsys.readouterr().out) == summary


def test_default_cli_freezes_pg4_sixteen_hour_pilot_after_pg3_verifier_rejection() -> None:
    pilot = discovery_cli._pilot_configuration()

    assert discovery_cli.DEFAULT_DERIVED_ROOT == Path("data/exports/derived/task14-PG-000004")
    assert discovery_cli.DEFAULT_PROGRAM_ROOT == Path("data/exports/discovery-programs/PG-000004")
    assert discovery_cli.DEFAULT_TRIAL_ROOT == Path("data/exports/trials/task14-PG-000004")
    assert pilot.selection_id == "SU-000704"
    assert pilot.preregistration_id == "PG-000004"
    assert pilot.split_id == "task14-first-real-discovery-v4"
    assert pilot.run_id == "DR-000704"
    assert pilot.dataset_snapshot_id == "DS-000704"
    assert pilot.feature_publication_id == "FP-000704"
    assert pilot.event_publication_id == "EP-000704"
    assert pilot.normalizer_id == "NZ-000704"
    assert pilot.regime_assignment_contract_id == "task14-contemporaneous-regimes-v4"
    assert pilot.start.isoformat() == "2025-02-01T00:00:00+00:00"
    assert pilot.discovery_end.isoformat() == "2025-02-01T08:00:00+00:00"
    assert pilot.development_end.isoformat() == "2025-02-01T16:00:00+00:00"
    assert pilot.holdout_end.isoformat() == "2025-02-02T00:00:00+00:00"
    assert pilot.motif_policy.policy_id == "task14-motif-research-v2"
    assert pilot.motif_policy.max_windows == 512
    assert pilot.program_budget.budget_id == "task14-program-budget-v4"
    assert pilot.phase3_execution.maximum_rows == 960
    assert pilot.run_max_rows == 480
    assert pilot.work_budget.budget_id == "task14-run-budget-v5"
    assert pilot.work_budget.maximum_materialized_rows == 959
    base_cells = pilot.work_budget.maximum_materialized_rows * len(pilot.feature_names)
    control_cells = (pilot.run_max_rows - 1) * len(pilot.feature_names)
    assert (base_cells, control_cells) == (5_754, 2_874)
    assert base_cells + control_cells == pilot.work_budget.maximum_feature_cells == 8_628
    assert pilot.run_max_rows * len(pilot.feature_names) == 2_880
    assert pilot.work_budget.maximum_pca_cells == 2_880
    assert pilot.work_budget.maximum_reliability_control_projection_cells == 1_437
    assert pilot.work_budget.maximum_aggregate_projection_cells == 4_317
    assert pilot.run_max_rows - min(pilot.motif_policy.window_lengths) + 1 <= 512
    assert pilot.started_at.isoformat() == "2026-07-22T20:00:00+00:00"
    assert pilot.completed_at.isoformat() == "2026-07-22T20:01:00+00:00"
