from __future__ import annotations

import inspect
from pathlib import Path
from types import SimpleNamespace

import pytest

from market_structure_lab.cli import publish_validation_aggregates_v2 as cli


def test_cli_requires_disabled_network() -> None:
    args = cli.build_parser().parse_args(
        [
            "--coverage-config",
            "coverage.json",
            "--split-config",
            "split.json",
            "--boundary-publication",
            "boundary.json",
            "--availability-root",
            "availability",
            "--minute-publication-root",
            "minute",
            "--minute-audit-root",
            "minute-audit",
            "--expected-minute-identity",
            "SRCV2-" + "a" * 64,
            "--network",
            "enabled",
            "--output-root",
            "out",
        ]
    )
    with pytest.raises(ValueError, match="disabled"):
        cli.run(args)


def test_cli_loads_verified_originals_and_publishes(monkeypatch, capsys) -> None:
    parent = SimpleNamespace(source_publication_identity=SimpleNamespace(value="SRCV2-" + "a" * 64))
    aggregate = SimpleNamespace(
        status=SimpleNamespace(value="available"),
        aggregate_identity=SimpleNamespace(value="AGGV2-" + "b" * 64),
        row_count=10,
        final_rows=0,
    )
    chain = (object(), object(), object())
    availability = object()
    monkeypatch.setattr(cli, "load_v2_boundary_publications", lambda **_kwargs: chain)
    monkeypatch.setattr(cli, "load_scoped_source_availability_v2", lambda **_kwargs: availability)
    monkeypatch.setattr(cli, "load_validation_source_publication_v2", lambda **_kwargs: parent)
    captured = {}

    def publish(**kwargs):
        captured.update(kwargs)
        return aggregate

    monkeypatch.setattr(cli, "publish_validation_aggregates_v2", publish)
    result = cli.main(
        [
            "--coverage-config",
            "coverage.json",
            "--split-config",
            "split.json",
            "--boundary-publication",
            "boundary.json",
            "--availability-root",
            "availability",
            "--minute-publication-root",
            "minute",
            "--minute-audit-root",
            "minute-audit",
            "--expected-minute-identity",
            parent.source_publication_identity.value,
            "--network",
            "disabled",
            "--output-root",
            "out",
        ]
    )
    assert result == 0
    assert captured["minute_publication"] is parent
    assert "available AGGV2-" in capsys.readouterr().out


def test_cli_has_no_database_network_final_or_pg_restore_path() -> None:
    source = Path(cli.__file__).read_text(encoding="utf-8")
    assert "reader" not in inspect.signature(cli.run).parameters
    assert "psycopg" not in source
    assert "urllib" not in source
    assert "--data-only" not in source
    assert "docker compose" not in source
    assert "final-holdout" not in source
