from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from market_structure_lab.cli.reconcile_candles import build_parser, main
from market_structure_lab.data.gaps import (
    ObservedEnvelope,
    ProvenanceState,
    RecoveryManifest,
    SourceIdentity,
    write_manifest,
)
from market_structure_lab.data.reconciliation import read_reconciliation_run


def _sha(character: str) -> str:
    return character * 64


def _compatibility(path: Path) -> RecoveryManifest:
    manifest = RecoveryManifest(
        manifest_version=1,
        source_identity=SourceIdentity(_sha("a"), 10, "mapping-v1"),
        as_of=datetime(2026, 7, 16, tzinfo=UTC).isoformat(),
        candidate_venue="binance",
        market_type="spot",
        envelopes=(
            ObservedEnvelope("BTCUSDT", "1m", 0, 120_000, 2),
            ObservedEnvelope("ETHUSDT", "1m", 60_000, 120_000, 1),
        ),
        gaps=(),
        provenance_validation={
            "BTCUSDT": ProvenanceState.SOURCE_CONFLICT,
            "ETHUSDT": ProvenanceState.SOURCE_CONFLICT,
        },
    )
    write_manifest(manifest, path)
    return manifest


def test_parser_exposes_complete_operator_surface() -> None:
    parser = build_parser()

    for operation in ("plan", "run", "promote", "report", "eligible"):
        with pytest.raises(SystemExit) as exit_info:
            parser.parse_args([operation, "--help"])
        assert exit_info.value.code == 0


def test_plan_freezes_selected_symbols_and_month_work_units(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    compatibility_path = tmp_path / "compatibility.json"
    compatibility = _compatibility(compatibility_path)
    output = tmp_path / "RR-000001.json"

    exit_code = main(
        [
            "plan",
            "--run-id",
            "RR-000001",
            "--compatibility",
            str(compatibility_path),
            "--compatibility-sha256",
            compatibility.sha256(),
            "--start",
            "2026-07-01T00:00:00Z",
            "--end",
            "2026-08-02T00:00:00Z",
            "--cutoff",
            "2026-08-02T00:01:00Z",
            "--symbol",
            "BTCUSDT",
            "--symbol",
            "ETHUSDT",
            "--code-commit",
            "1ee3a62",
            "--uv-lock-sha256",
            _sha("b"),
            "--output",
            str(output),
        ]
    )

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    run = read_reconciliation_run(output)
    assert payload["run_id"] == "RR-000001"
    assert payload["symbols"] == 2
    assert payload["work_units"] == 4
    assert {item.symbol for item in run.envelopes} == {"BTCUSDT", "ETHUSDT"}
    assert run.dump_sha256 == _sha("a")
    assert run.algorithm_version == "row-reconciliation-v2"


def test_plan_full_envelopes_uses_each_symbols_first_observation_to_cutoff(
    tmp_path: Path,
) -> None:
    compatibility_path = tmp_path / "compatibility.json"
    compatibility = _compatibility(compatibility_path)
    output = tmp_path / "RR-000099.json"

    exit_code = main(
        [
            "plan",
            "--run-id",
            "RR-000099",
            "--compatibility",
            str(compatibility_path),
            "--compatibility-sha256",
            compatibility.sha256(),
            "--full-envelopes",
            "--cutoff",
            "1970-02-01T00:00:00Z",
            "--code-commit",
            "deadbeef",
            "--uv-lock-sha256",
            _sha("b"),
            "--output",
            str(output),
        ]
    )

    assert exit_code == 0
    run = read_reconciliation_run(output)
    by_symbol = {item.symbol: item for item in run.envelopes}
    assert by_symbol["BTCUSDT"].start_ms == 0
    assert by_symbol["ETHUSDT"].start_ms == 60_000
    assert {item.end_ms for item in run.envelopes} == {2_678_400_000}
    assert len(run.work_units) == 2


def test_plan_rejects_unknown_symbols_and_changed_compatibility_hash(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    compatibility_path = tmp_path / "compatibility.json"
    compatibility = _compatibility(compatibility_path)
    common = [
        "plan",
        "--run-id",
        "RR-000001",
        "--compatibility",
        str(compatibility_path),
        "--start",
        "2026-07-01T00:00:00Z",
        "--end",
        "2026-08-01T00:00:00Z",
        "--cutoff",
        "2026-08-01T00:01:00Z",
        "--code-commit",
        "1ee3a62",
        "--uv-lock-sha256",
        _sha("b"),
        "--output",
        str(tmp_path / "run.json"),
    ]

    assert main([*common, "--compatibility-sha256", _sha("f"), "--symbol", "BTCUSDT"]) == 1
    assert "checksum" in capsys.readouterr().err
    assert (
        main(
            [
                *common,
                "--compatibility-sha256",
                compatibility.sha256(),
                "--symbol",
                "SOLUSDT",
            ]
        )
        == 1
    )
    assert "absent" in capsys.readouterr().err


def test_run_and_promote_require_explicit_apply() -> None:
    parser = build_parser()

    run = parser.parse_args(["run", "--run", "x.json", "--output-root", "out"])
    promote = parser.parse_args(["promote", "--run", "x.json", "--output-root", "out"])
    assert run.apply is False
    assert promote.apply is False
