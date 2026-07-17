from __future__ import annotations

import json
from contextlib import nullcontext
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

from market_structure_lab.cli import sync_candles
from market_structure_lab.data.freshness import write_freshness_manifest
from market_structure_lab.data.freshness_sync import (
    build_freshness_report,
    write_freshness_artifacts,
)
from market_structure_lab.data.gaps import ProvenanceState, write_manifest

from test_freshness_sync import _compatibility, _manifest, _plan


def _write_inputs(tmp_path: Path) -> tuple[Path, Path, str]:
    freshness = _manifest()
    compatibility = _compatibility()
    manifest_path = tmp_path / "freshness.json"
    compatibility_path = tmp_path / "compatibility.json"
    write_freshness_manifest(freshness, manifest_path)
    write_manifest(compatibility, compatibility_path)
    return manifest_path, compatibility_path, compatibility.sha256()


def _blocked_but_gap_complete_manifest():
    current = _manifest(recovered=True)
    eth = _plan("ETHUSDT", ProvenanceState.SOURCE_CONFLICT, (0, 120_000), 3, ())
    symbols = tuple(eth if item.symbol == "ETHUSDT" else item for item in current.symbols)
    return replace(
        current,
        canonical_row_count=sum(item.canonical_state.row_count for item in symbols),
        symbols=symbols,
    )


def test_run_dry_run_reads_frozen_evidence_without_database_or_source_writes(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    manifest, compatibility, compatibility_sha = _write_inputs(tmp_path)
    monkeypatch.setattr(
        sync_candles,
        "create_engine",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("database opened")),
    )
    monkeypatch.setattr(
        sync_candles,
        "run_freshness_sync",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("source fetched")),
    )

    exit_code = sync_candles.main(
        [
            "run",
            "--manifest",
            str(manifest),
            "--compatibility",
            str(compatibility),
            "--compatibility-sha256",
            compatibility_sha,
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 2
    assert payload["dry_run"] is True
    assert payload["missing_minutes"] == 3
    assert payload["eligible_minutes"] == 1
    assert payload["symbols"] == 3


def test_run_dry_run_exits_stale_when_gap_complete_symbol_is_provenance_blocked(
    tmp_path: Path, capsys
) -> None:
    manifest = _blocked_but_gap_complete_manifest()
    compatibility = _compatibility()
    manifest_path = tmp_path / "freshness.json"
    compatibility_path = tmp_path / "compatibility.json"
    write_freshness_manifest(manifest, manifest_path)
    write_manifest(compatibility, compatibility_path)

    exit_code = sync_candles.main(
        [
            "run",
            "--manifest",
            str(manifest_path),
            "--compatibility",
            str(compatibility_path),
            "--compatibility-sha256",
            compatibility.sha256(),
        ]
    )

    assert json.loads(capsys.readouterr().out)["missing_minutes"] == 0
    assert exit_code == 2


def test_plan_exits_stale_when_gap_complete_symbol_is_provenance_blocked(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    monkeypatch.setenv("POSTGRES_PASSWORD", "test-only")
    manifest = _blocked_but_gap_complete_manifest()
    compatibility = _compatibility()

    class FakeEngine:
        def connect(self):
            return nullcontext(object())

        def dispose(self) -> None:
            return None

    monkeypatch.setattr(sync_candles, "EXPECTED_DUMP_SHA256", "a" * 64)
    monkeypatch.setattr(
        sync_candles,
        "_read_reviewed_compatibility",
        lambda *args: compatibility,
    )
    monkeypatch.setattr(sync_candles, "create_engine", lambda *args, **kwargs: FakeEngine())
    monkeypatch.setattr(sync_candles, "verify_manifest_identity", lambda *args, **kwargs: None)
    monkeypatch.setattr(sync_candles, "build_freshness_manifest", lambda *args, **kwargs: manifest)
    monkeypatch.setattr(
        sync_candles,
        "_write_plan_artifact",
        lambda *args: tmp_path / "blocked.plan.json",
    )

    exit_code = sync_candles.main(
        [
            "plan",
            "--compatibility",
            str(tmp_path / "compatibility.json"),
            "--compatibility-sha256",
            compatibility.sha256(),
            "--output-dir",
            str(tmp_path),
        ]
    )

    assert json.loads(capsys.readouterr().out)["missing_minutes"] == 0
    assert exit_code == 2


def test_apply_rejects_dump_mismatch_before_database_migration_or_source(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    manifest, compatibility, compatibility_sha = _write_inputs(tmp_path)
    dump = tmp_path / "callscore.dump"
    dump.write_bytes(b"not the immutable dump")
    monkeypatch.setattr(sync_candles, "EXPECTED_DUMP_SHA256", "a" * 64)
    monkeypatch.setattr(sync_candles, "sha256_file", lambda path: "f" * 64)
    monkeypatch.setattr(
        sync_candles,
        "create_engine",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("database opened")),
    )

    exit_code = sync_candles.main(
        [
            "run",
            "--manifest",
            str(manifest),
            "--compatibility",
            str(compatibility),
            "--compatibility-sha256",
            compatibility_sha,
            "--dump-path",
            str(dump),
            "--apply",
        ]
    )

    assert exit_code == 1
    assert "dump SHA-256" in capsys.readouterr().err


def test_tampered_or_unpinned_compatibility_fails_before_dry_run(capsys, tmp_path: Path) -> None:
    manifest, compatibility, _ = _write_inputs(tmp_path)

    exit_code = sync_candles.main(
        [
            "run",
            "--manifest",
            str(manifest),
            "--compatibility",
            str(compatibility),
            "--compatibility-sha256",
            "0" * 64,
        ]
    )

    assert exit_code == 1
    assert "reviewed compatibility" in capsys.readouterr().err


def test_report_and_health_return_stale_exit_code_and_secret_safe_json(
    tmp_path: Path, capsys
) -> None:
    manifest = _manifest()
    report = build_freshness_report(manifest, manifest)
    paths = write_freshness_artifacts(manifest, report, tmp_path)

    report_exit = sync_candles.main(["report", "--report", str(paths.report)])
    report_payload = json.loads(capsys.readouterr().out)
    health_exit = sync_candles.main(["health", "--output-dir", str(tmp_path)])
    health_payload = json.loads(capsys.readouterr().out)

    assert report_exit == 2
    assert health_exit == 2
    assert report_payload["after_missing_minutes"] == 3
    assert health_payload == {
        "as_of": "2026-07-16T12:34:00Z",
        "current": False,
        "missing_minutes": 3,
        "report_sha256": report.sha256(),
        "symbols_current": 1,
        "symbols_total": 3,
    }
    assert "password" not in json.dumps(report_payload).lower()


def test_cli_parser_and_package_entry_point_expose_all_scheduler_operations() -> None:
    parser = sync_candles.build_parser()
    help_text = parser.format_help()

    for operation in ("bootstrap", "plan", "run", "report", "health", "snapshot"):
        assert operation in help_text


def test_bootstrap_is_idempotent_and_does_not_fetch_source(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    monkeypatch.setenv("POSTGRES_PASSWORD", "test-only")
    compatibility = _compatibility()
    compatibility_path = tmp_path / "compatibility.json"
    write_manifest(compatibility, compatibility_path)
    dump = tmp_path / "callscore.dump"
    dump.write_bytes(b"immutable test dump")
    calls = {"migrations": 0}

    class MigrationConnection:
        def execute(self, _statement) -> None:
            calls["migrations"] += 1

    class FakeEngine:
        def connect(self):
            return nullcontext(object())

        def begin(self):
            return nullcontext(MigrationConnection())

        def dispose(self) -> None:
            return None

    monkeypatch.setattr(sync_candles, "EXPECTED_DUMP_SHA256", "a" * 64)
    monkeypatch.setattr(sync_candles, "sha256_file", lambda _path: "a" * 64)
    monkeypatch.setattr(sync_candles, "create_engine", lambda *_args, **_kwargs: FakeEngine())
    monkeypatch.setattr(sync_candles, "verify_manifest_identity", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        sync_candles,
        "BinanceSpotSource",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("source fetched")),
    )

    arguments = [
        "bootstrap",
        "--compatibility",
        str(compatibility_path),
        "--compatibility-sha256",
        compatibility.sha256(),
        "--dump-path",
        str(dump),
    ]
    first = sync_candles.main(arguments)
    first_payload = json.loads(capsys.readouterr().out)
    second = sync_candles.main(arguments)
    second_payload = json.loads(capsys.readouterr().out)

    assert first == second == 0
    assert first_payload == second_payload
    assert first_payload["status"] == "ready"
    assert calls["migrations"] == 2


def test_bootstrap_rejects_dump_mismatch_before_database_open(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    dump = tmp_path / "callscore.dump"
    dump.write_bytes(b"wrong dump")
    monkeypatch.setattr(sync_candles, "sha256_file", lambda _path: "f" * 64)
    monkeypatch.setattr(
        sync_candles,
        "create_engine",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("database opened")),
    )

    exit_code = sync_candles.main(
        [
            "bootstrap",
            "--compatibility",
            str(tmp_path / "compatibility.json"),
            "--compatibility-sha256",
            "a" * 64,
            "--dump-path",
            str(dump),
        ]
    )

    assert exit_code == 1
    assert "dump SHA-256" in capsys.readouterr().err


def test_snapshot_is_an_explicit_checksum_verified_handoff(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    monkeypatch.setenv("POSTGRES_PASSWORD", "test-only")
    current = _manifest(recovered=True)
    report = build_freshness_report(
        current,
        current,
        recovery_logical_hash="d" * 64,
    )
    paths = write_freshness_artifacts(current, report, tmp_path / "freshness")
    compatibility = _compatibility()
    compatibility_path = tmp_path / "compatibility.json"
    write_manifest(compatibility, compatibility_path)
    dump = tmp_path / "callscore.dump"
    dump.write_bytes(b"immutable test dump")
    calls: dict[str, object] = {}

    class FakeEngine:
        def connect(self):
            return nullcontext(object())

        def dispose(self) -> None:
            return None

    monkeypatch.setattr(sync_candles, "EXPECTED_DUMP_SHA256", "a" * 64)
    monkeypatch.setattr(sync_candles, "sha256_file", lambda _path: "a" * 64)
    monkeypatch.setattr(sync_candles, "create_engine", lambda *_args, **_kwargs: FakeEngine())
    monkeypatch.setattr(sync_candles, "verify_manifest_identity", lambda *_args, **_kwargs: None)

    def fake_publish(engine, actual_report, **kwargs):
        calls.update(engine=engine, report=actual_report, **kwargs)
        return SimpleNamespace(
            identity=SimpleNamespace(dataset_version=kwargs["dataset_version"]),
            row_count=123,
            snapshot_sha256="e" * 64,
        )

    monkeypatch.setattr(sync_candles, "publish_freshness_snapshot", fake_publish)

    exit_code = sync_candles.main(
        [
            "snapshot",
            "--report",
            str(paths.report),
            "--compatibility",
            str(compatibility_path),
            "--compatibility-sha256",
            compatibility.sha256(),
            "--dump-path",
            str(dump),
            "--output-root",
            str(tmp_path / "snapshots"),
            "--dataset-version",
            "canonical-20260716",
            "--config-version",
            "freshness-snapshot-v1",
            "--code-commit",
            "0123456789abcdef",
            "--allow-provenance-blocked",
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert calls["report"] == report
    assert calls["dataset_version"] == "canonical-20260716"
    assert str(calls["policy"]) == "allow_provenance_blocked"
    assert payload["row_count"] == 123
    assert payload["freshness_report_sha256"] == report.sha256()
