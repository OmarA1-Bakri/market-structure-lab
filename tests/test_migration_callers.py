from __future__ import annotations

import ast
from pathlib import Path

import pytest

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


def _function_call_names(path: str, function_name: str) -> list[str]:
    tree = ast.parse((REPOSITORY_ROOT / path).read_text(encoding="utf-8"))
    function = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == function_name
    )
    return [
        node.func.id
        for node in ast.walk(function)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    ]


@pytest.mark.parametrize(
    ("path", "function_name", "helper"),
    (
        (
            "src/market_structure_lab/cli/reconcile_candles.py",
            "_run",
            "prepare_reconciliation_schema",
        ),
        (
            "src/market_structure_lab/cli/reconcile_candles.py",
            "_promote",
            "prepare_reconciliation_schema",
        ),
        ("src/market_structure_lab/cli/sync_candles.py", "_bootstrap", "prepare_recovery_schema"),
        ("src/market_structure_lab/cli/sync_candles.py", "_run", "prepare_recovery_schema"),
        ("src/market_structure_lab/cli/backfill_candles.py", "_fetch", "prepare_recovery_schema"),
    ),
)
def test_cli_migration_callers_use_centralized_schema_preparation(
    path: str,
    function_name: str,
    helper: str,
) -> None:
    calls = _function_call_names(path, function_name)

    assert helper in calls
    assert "candle_recovery_migration_sql" not in calls
    assert "candle_reconciliation_migration_sql" not in calls
