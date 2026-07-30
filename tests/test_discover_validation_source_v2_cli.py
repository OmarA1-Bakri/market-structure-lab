from __future__ import annotations

from pathlib import Path

import pytest

from market_structure_lab.cli import discover_validation_source_v2 as cli


def test_cli_requires_disabled_network() -> None:
    parser = cli.build_parser()
    args = parser.parse_args(
        [
            "--coverage-config",
            "coverage.json",
            "--split-config",
            "split.json",
            "--boundary-publication",
            "boundary.json",
            "--audit-ledger-root",
            "audit",
            "--candidate-root",
            "candidates",
            "--network",
            "enabled",
            "--output-root",
            "out",
        ]
    )
    with pytest.raises(ValueError, match="disabled"):
        cli.run(args)


def test_cli_has_no_database_initialization_or_pg_restore_row_path() -> None:
    source = Path(cli.__file__).read_text(encoding="utf-8")
    assert "docker compose" not in source
    assert "--data-only" not in source
