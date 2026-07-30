from __future__ import annotations

from datetime import UTC, datetime
import hashlib
import os
from pathlib import Path

import pytest

from market_structure_lab.cli import freeze_validation_boundary_v2 as cli
from market_structure_lab.data.gaps import (
    ObservedEnvelope,
    ProvenanceState,
    RecoveryManifest,
    SourceIdentity,
)
from market_structure_lab.data.reconciliation.manifests import (
    TradingEnvelope,
    WorkUnitManifest,
    freeze_reconciliation_run,
)
from market_structure_lab.data.reconciliation.models import ReconciliationWorkUnit
from market_structure_lab.data.reconciliation.receipts import (
    write_reconciliation_promotion_receipt,
)
from market_structure_lab.data.reconciliation.repository import (
    ReconciliationPromotion,
    VerifiedCoverageInterval,
)
from market_structure_lab.research.validation_v2_models import (
    SourceCoveragePublicationV2,
)
from market_structure_lab.research.validation_v2_splits import (
    DevelopmentReadBoundaryV2,
    DevelopmentSplitPublicationV2,
)


def _work_manifest(run_id: str, unit: ReconciliationWorkUnit) -> WorkUnitManifest:
    values = {
        "run_id": run_id,
        "work_unit_id": unit.work_unit_id,
        "publication_path": f"work-units/{unit.work_unit_id}",
        "row_count": 0,
        "classification_counts": (),
        "differing_field_counts": (),
        "source_artifacts": (),
        "replacement_row_count": 0,
        "replacement_logical_sha256": hashlib.sha256(b"").hexdigest(),
        "max_rows_per_part": 1,
        "max_buffered_rows": 0,
        "status": "completed",
        "parts": (),
    }
    provisional = WorkUnitManifest.__new__(WorkUnitManifest)
    for name, value in values.items():
        object.__setattr__(provisional, name, value)
    object.__setattr__(provisional, "manifest_sha256", "0" * 64)
    return WorkUnitManifest(**values, manifest_sha256=provisional.sha256())  # type: ignore[arg-type]


def _fixture(tmp_path: Path) -> dict[str, object]:
    dump = tmp_path / "callscore.dump"
    dump.write_bytes(b"fixture-postgresql-custom-dump")
    dump_sha = hashlib.sha256(dump.read_bytes()).hexdigest()
    start = int(datetime(2023, 1, 1, tzinfo=UTC).timestamp() * 1000)
    end = int(datetime(2026, 1, 1, tzinfo=UTC).timestamp() * 1000)
    symbols = ("ADAUSDT", "BNBUSDT", "DOGEUSDT", "SOLUSDT", "XRPUSDT")
    units = tuple(ReconciliationWorkUnit.create(symbol, "1m", start, end) for symbol in symbols)
    run = freeze_reconciliation_run(
        run_id="RR-000008",
        cutoff=datetime(2026, 1, 1, tzinfo=UTC),
        dump_sha256=dump_sha,
        source_row_count=0,
        mapping_version="callscore-candles-v1",
        candidate_venue="binance",
        market_type="spot",
        source_revision="fixture",
        algorithm_version="v1",
        code_commit="1" * 40,
        uv_lock_sha256="2" * 64,
        envelopes=tuple(TradingEnvelope(symbol, "1m", start, end) for symbol in symbols),
        work_units=units,
    )
    rr_root = tmp_path / "rr"
    rr_root.mkdir()
    (rr_root / "run.json").write_text(run.to_json(), encoding="utf-8")
    for unit in units:
        directory = rr_root / "work-units" / unit.work_unit_id
        directory.mkdir(parents=True)
        (directory / "manifest.json").write_text(
            _work_manifest(run.run_id, unit).to_json(), encoding="utf-8"
        )
    coverage = tuple(VerifiedCoverageInterval(symbol, "1m", start, end) for symbol in symbols)
    promotion = ReconciliationPromotion(
        run_id=run.run_id,
        manifest_sha256=run.manifest_sha256,
        replacement_logical_sha256="3" * 64,
        canonical_logical_sha256="4" * 64,
        promoted_at="2026-01-02T00:00:00Z",
    )
    receipt = write_reconciliation_promotion_receipt(
        tmp_path / "receipts", run, promotion, coverage
    )
    compatibility_manifest = RecoveryManifest(
        manifest_version=1,
        source_identity=SourceIdentity(
            dump_sha256=dump_sha,
            source_row_count=0,
            mapping_version="callscore-candles-v1",
        ),
        as_of="2026-01-02T00:00:00Z",
        candidate_venue="binance",
        market_type="spot",
        envelopes=tuple(
            ObservedEnvelope(
                symbol=symbol,
                timeframe="1m",
                first_open_time_ms=start,
                last_open_time_ms=end - 60_000,
                row_count=1,
            )
            for symbol in symbols
        ),
        gaps=(),
        provenance_validation={symbol: ProvenanceState.COMPATIBLE for symbol in symbols},
    )
    compatibility = tmp_path / "compatibility.json"
    compatibility.write_bytes(compatibility_manifest.canonical_bytes())
    listing = b"42; 0 0 TABLE DATA public candles fixture\n"
    expectations = cli._AuthorityExpectationsV2(  # noqa: SLF001
        dump_sha256=dump_sha,
        promotion_sha256=hashlib.sha256(receipt.path.read_bytes()).hexdigest(),
        compatibility_sha256=hashlib.sha256(compatibility.read_bytes()).hexdigest(),
        pg_restore_list=lambda _path: listing,
    )
    return {
        "dump_path": dump,
        "rr_promotion": receipt.path,
        "rr_root": rr_root,
        "compatibility": compatibility,
        "expectations": expectations,
    }


def _outputs(tmp_path: Path, prefix: str) -> dict[str, Path]:
    return {
        "output_coverage": tmp_path / f"{prefix}-coverage.json",
        "output_split": tmp_path / f"{prefix}-split.json",
        "output_boundary": tmp_path / f"{prefix}-boundary.json",
    }


def _freeze(tmp_path: Path, prefix: str = "published"):
    fixture = _fixture(tmp_path)
    return cli.freeze_boundary_publications_v2(
        dump_path=fixture["dump_path"],  # type: ignore[arg-type]
        rr_promotion=fixture["rr_promotion"],  # type: ignore[arg-type]
        rr_root=fixture["rr_root"],  # type: ignore[arg-type]
        compatibility=fixture["compatibility"],  # type: ignore[arg-type]
        **_outputs(tmp_path, prefix),
        require_zero_row_access=True,
        require_zero_process_row_extraction=True,
        require_zero_network_access=True,
        _test_expectations=fixture["expectations"],  # type: ignore[arg-type]
    )


def test_freezer_recomputes_original_metadata_and_reopens_publications(
    tmp_path: Path,
) -> None:
    result = _freeze(tmp_path)

    coverage = SourceCoveragePublicationV2.from_dict(
        __import__("json").loads(result.coverage_path.read_bytes())
    )
    split = DevelopmentSplitPublicationV2.from_dict(
        __import__("json").loads(result.split_path.read_bytes()), coverage
    )
    boundary = DevelopmentReadBoundaryV2.from_publication_dict(
        __import__("json").loads(result.boundary_path.read_bytes()), coverage, split
    )
    assert boundary == result.boundary
    assert (
        coverage.raw_dump.dump_sha256
        == hashlib.sha256((tmp_path / "callscore.dump").read_bytes()).hexdigest()
    )


def test_freezer_rejects_coherent_forged_compatibility_summary(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    compatibility = fixture["compatibility"]
    content = compatibility.read_bytes().replace(b'"compatible"', b'"source_conflict"')
    compatibility.write_bytes(content)

    with pytest.raises(ValueError, match="compatibility SHA-256"):
        cli.freeze_boundary_publications_v2(
            dump_path=fixture["dump_path"],  # type: ignore[arg-type]
            rr_promotion=fixture["rr_promotion"],  # type: ignore[arg-type]
            rr_root=fixture["rr_root"],  # type: ignore[arg-type]
            compatibility=compatibility,  # type: ignore[arg-type]
            **_outputs(tmp_path, "forged"),
            require_zero_row_access=True,
            require_zero_process_row_extraction=True,
            require_zero_network_access=True,
            _test_expectations=fixture["expectations"],  # type: ignore[arg-type]
        )


def test_freezer_requires_all_zero_access_guards(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    with pytest.raises(ValueError, match="zero-row"):
        cli.freeze_boundary_publications_v2(
            dump_path=fixture["dump_path"],  # type: ignore[arg-type]
            rr_promotion=fixture["rr_promotion"],  # type: ignore[arg-type]
            rr_root=fixture["rr_root"],  # type: ignore[arg-type]
            compatibility=fixture["compatibility"],  # type: ignore[arg-type]
            **_outputs(tmp_path, "unguarded"),
            require_zero_row_access=False,
            require_zero_process_row_extraction=True,
            require_zero_network_access=True,
            _test_expectations=fixture["expectations"],  # type: ignore[arg-type]
        )


def test_freezer_is_byte_deterministic_and_no_clobber(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    common = {
        "dump_path": fixture["dump_path"],
        "rr_promotion": fixture["rr_promotion"],
        "rr_root": fixture["rr_root"],
        "compatibility": fixture["compatibility"],
        "require_zero_row_access": True,
        "require_zero_process_row_extraction": True,
        "require_zero_network_access": True,
        "_test_expectations": fixture["expectations"],
    }
    first_paths = _outputs(tmp_path, "first")
    second_paths = _outputs(tmp_path, "second")
    cli.freeze_boundary_publications_v2(**common, **first_paths)  # type: ignore[arg-type]
    cli.freeze_boundary_publications_v2(**common, **second_paths)  # type: ignore[arg-type]

    assert [path.read_bytes() for path in first_paths.values()] == [
        path.read_bytes() for path in second_paths.values()
    ]
    with pytest.raises(FileExistsError):
        cli.freeze_boundary_publications_v2(**common, **first_paths)  # type: ignore[arg-type]


def test_freezer_rejects_existing_v1_programme_root(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    root = tmp_path / ("VP-0d65fef04ca44dfc5ba7c7705e0be197480d7456b51178702a0011a18ab4487d")
    root.mkdir()
    with pytest.raises(ValueError, match="V1 programme root"):
        cli.freeze_boundary_publications_v2(
            dump_path=fixture["dump_path"],  # type: ignore[arg-type]
            rr_promotion=fixture["rr_promotion"],  # type: ignore[arg-type]
            rr_root=fixture["rr_root"],  # type: ignore[arg-type]
            compatibility=fixture["compatibility"],  # type: ignore[arg-type]
            output_coverage=root / "coverage.json",
            output_split=root / "split.json",
            output_boundary=root / "boundary.json",
            require_zero_row_access=True,
            require_zero_process_row_extraction=True,
            require_zero_network_access=True,
            _test_expectations=fixture["expectations"],  # type: ignore[arg-type]
        )


def test_concurrent_output_is_not_replaced_and_partial_outputs_are_rolled_back(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path)
    outputs = _outputs(tmp_path, "race")
    original_link = os.link
    calls = 0

    def racing_link(source: Path, destination: Path, **kwargs: object) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            Path(destination).write_bytes(b"concurrent")
        original_link(source, destination, **kwargs)

    monkeypatch.setattr(cli.os, "link", racing_link)
    with pytest.raises(FileExistsError):
        cli.freeze_boundary_publications_v2(
            dump_path=fixture["dump_path"],  # type: ignore[arg-type]
            rr_promotion=fixture["rr_promotion"],  # type: ignore[arg-type]
            rr_root=fixture["rr_root"],  # type: ignore[arg-type]
            compatibility=fixture["compatibility"],  # type: ignore[arg-type]
            **outputs,
            require_zero_row_access=True,
            require_zero_process_row_extraction=True,
            require_zero_network_access=True,
            _test_expectations=fixture["expectations"],  # type: ignore[arg-type]
        )
    assert not outputs["output_coverage"].exists()
    assert outputs["output_split"].read_bytes() == b"concurrent"
    assert not outputs["output_boundary"].exists()


def test_parser_exposes_exact_plan_arguments() -> None:
    parser = cli.build_parser()
    args = parser.parse_args(
        [
            "--dump-path",
            "dump",
            "--rr-promotion",
            "promotion",
            "--rr-root",
            "rr",
            "--compatibility",
            "compatibility",
            "--output-coverage",
            "coverage",
            "--output-split",
            "split",
            "--output-boundary",
            "boundary",
            "--require-zero-row-access",
            "--require-zero-process-row-extraction",
            "--require-zero-network-access",
        ]
    )

    assert args.require_zero_row_access is True
    assert args.require_zero_process_row_extraction is True
    assert args.require_zero_network_access is True
