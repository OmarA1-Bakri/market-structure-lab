from __future__ import annotations

import inspect
from pathlib import Path
from types import SimpleNamespace

import pytest

from market_structure_lab.cli import publish_validation_cost_authority_v2 as cli


def _argv(*, network: str = "disabled") -> list[str]:
    return [
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
        "--aggregate-publication-root",
        "aggregate",
        "--expected-aggregate-identity",
        "AGGV2-" + "b" * 64,
        "--archive-manifest",
        "archive-manifest.json",
        "--archive-publication-root",
        "archive-publication",
        "--archive-cache-root",
        "archive-cache",
        "--network",
        network,
        "--output-root",
        "cost-authority",
    ]


def test_cli_requires_disabled_network() -> None:
    with pytest.raises(ValueError, match="disabled"):
        cli.run(cli.build_parser().parse_args(_argv(network="enabled")))


def test_cli_loads_verified_original_chain_and_publishes(monkeypatch, capsys) -> None:
    chain = (object(), object(), object())
    availability = object()
    minute = SimpleNamespace(source_publication_identity=SimpleNamespace(value="SRCV2-" + "a" * 64))
    aggregate = SimpleNamespace(aggregate_identity=SimpleNamespace(value="AGGV2-" + "b" * 64))
    manifest = object()
    acquisition = object()
    authority = SimpleNamespace(
        evaluation_status=SimpleNamespace(value="completed"),
        conclusion=SimpleNamespace(value="inconclusive"),
        cost_identity=SimpleNamespace(value="CSTV2-" + "c" * 64),
    )
    monkeypatch.setattr(cli, "load_v2_boundary_publications", lambda **k: chain)
    monkeypatch.setattr(cli, "load_scoped_source_availability_v2", lambda **k: availability)
    monkeypatch.setattr(cli, "load_validation_source_publication_v2", lambda **k: minute)
    monkeypatch.setattr(cli, "load_validation_aggregate_publication_v2", lambda **k: aggregate)
    monkeypatch.setattr(cli, "load_binance_archive_request_manifest_v2", lambda **k: manifest)
    monkeypatch.setattr(cli, "load_binance_archive_acquisition_v2", lambda **k: acquisition)
    captured = {}
    monkeypatch.setattr(
        cli,
        "publish_validation_cost_authority_v2",
        lambda **kwargs: captured.update(kwargs) or authority,
    )
    assert cli.main(_argv()) == 0
    assert captured["source_publication"] is minute
    assert captured["aggregate_publication"] is aggregate
    assert captured["archive_manifest"] is manifest
    assert captured["archive_acquisition"] is acquisition
    assert "completed inconclusive CSTV2-" in capsys.readouterr().out


def test_cli_has_no_network_database_final_or_pg_restore_path() -> None:
    source = Path(cli.__file__).read_text(encoding="utf-8")
    assert "reader" not in inspect.signature(cli.run).parameters
    assert "psycopg" not in source
    assert "urllib" not in source
    assert "--data-only" not in source
    assert "docker compose" not in source
    assert "final-holdout" not in source
