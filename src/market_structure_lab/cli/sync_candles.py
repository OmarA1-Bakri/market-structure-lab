"""Non-interactive daily planning, append-only sync, reporting, and health CLI."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.exc import SQLAlchemyError

from market_structure_lab.core.config import load_settings
from market_structure_lab.data.export import sha256_file
from market_structure_lab.data.freshness import (
    FreshnessManifest,
    FreshnessPlanningStatus,
    build_freshness_manifest,
    read_freshness_manifest,
    resolve_freshness_cutoff,
    write_freshness_manifest,
)
from market_structure_lab.data.freshness_sync import (
    FreshnessRunStatus,
    build_freshness_recovery_manifest,
    freshness_exit_code,
    read_freshness_report,
    read_latest_freshness_report,
    run_freshness_sync,
    write_freshness_artifacts,
)
from market_structure_lab.data.freshness_snapshot import (
    SnapshotPublicationPolicy,
    publish_freshness_snapshot,
)
from market_structure_lab.data.gaps import RecoveryManifest, read_manifest, verify_manifest_identity
from market_structure_lab.data.migrations import candle_recovery_migration_sql
from market_structure_lab.data.sources.base import SourceError
from market_structure_lab.data.sources.binance import BinanceSpotSource

EXPECTED_DUMP_SHA256 = "1b6bcb39af41048b53729e9b094f0229163eb6ff6af9563adb666c96f5fd4da4"
DEFAULT_OUTPUT_DIR = Path("data/exports/freshness")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Keep canonical one-minute cryptocurrency candles current"
    )
    commands = parser.add_subparsers(dest="operation", required=True)

    plan = commands.add_parser("plan", help="freeze exact canonical gaps at one UTC cutoff")
    _add_compatibility_arguments(plan)
    plan.add_argument("--as-of", help="minute-aligned UTC replay cutoff (default: current minute)")
    plan.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)

    run = commands.add_parser("run", help="inspect or apply one frozen freshness plan")
    run.add_argument("--manifest", type=Path, required=True)
    _add_compatibility_arguments(run)
    run.add_argument("--cache-dir", type=Path, default=Path("data/cache/binance"))
    run.add_argument("--dump-path", type=Path, default=Path("data/dumps/callscore.dump"))
    run.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    run.add_argument(
        "--apply",
        action="store_true",
        help="verify immutable inputs, apply the migration, and publish real supplements",
    )

    report = commands.add_parser("report", help="emit a checksum-verified freshness report")
    report.add_argument("--report", type=Path)
    report.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)

    health = commands.add_parser("health", help="emit compact scheduler health JSON")
    health.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    snapshot = commands.add_parser(
        "snapshot",
        help="deliberately freeze a verified freshness report as immutable Parquet",
    )
    snapshot.add_argument("--report", type=Path, required=True)
    _add_compatibility_arguments(snapshot)
    snapshot.add_argument("--dump-path", type=Path, default=Path("data/dumps/callscore.dump"))
    snapshot.add_argument("--output-root", type=Path, default=Path("data/exports/canonical"))
    snapshot.add_argument("--dataset-version", required=True)
    snapshot.add_argument("--config-version", required=True)
    snapshot.add_argument("--code-commit", required=True)
    snapshot.add_argument("--batch-size", type=int, default=10_000)
    snapshot.add_argument(
        "--allow-provenance-blocked",
        action="store_true",
        help="publish only when every non-healthy symbol is explicitly provenance-blocked",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.operation == "plan":
            return _plan(args)
        if args.operation == "run":
            return _run(args)
        if args.operation == "report":
            return _report(args)
        if args.operation == "health":
            return _health(args)
        return _snapshot(args)
    except (OSError, RuntimeError, ValueError, SourceError, SQLAlchemyError) as error:
        print(str(error), file=sys.stderr)
        return 1


def _plan(args: argparse.Namespace) -> int:
    compatibility = _read_reviewed_compatibility(
        args.compatibility,
        args.compatibility_sha256,
    )
    if compatibility.source_identity.dump_sha256.lower() != EXPECTED_DUMP_SHA256:
        raise ValueError("reviewed compatibility does not identify the immutable source dump")
    cutoff = resolve_freshness_cutoff(
        as_of=(
            None
            if args.as_of is None
            else datetime.fromisoformat(args.as_of.replace("Z", "+00:00"))
        )
    )
    settings = load_settings()
    engine = create_engine(settings.database.url)
    try:
        with engine.connect() as connection:
            verify_manifest_identity(
                connection,
                compatibility,
                dump_sha256=compatibility.source_identity.dump_sha256,
                mapping_version=settings.candles.version,
                schema=settings.candles.schema,
                table=settings.candles.table,
            )
            manifest = build_freshness_manifest(
                connection,
                dump_identity=compatibility.source_identity,
                compatibility_manifest=compatibility,
                compatibility_manifest_sha256=args.compatibility_sha256.lower(),
                as_of=cutoff,
            )
    finally:
        engine.dispose()
    path = _write_plan_artifact(manifest, args.output_dir)
    payload = _plan_payload(manifest)
    print(_json({"manifest": str(path), "manifest_sha256": manifest.sha256(), **payload}))
    return _planning_exit_code(manifest)


def _run(args: argparse.Namespace) -> int:
    manifest = read_freshness_manifest(args.manifest)
    compatibility = _read_reviewed_compatibility(
        args.compatibility,
        args.compatibility_sha256,
    )
    recovery = build_freshness_recovery_manifest(manifest, compatibility)
    if not args.apply:
        print(
            _json(
                {"dry_run": True, "manifest_sha256": manifest.sha256(), **_plan_payload(manifest)}
            )
        )
        return _planning_exit_code(manifest)

    actual_dump_sha = sha256_file(args.dump_path)
    if manifest.dump_identity.dump_sha256.lower() != EXPECTED_DUMP_SHA256:
        raise ValueError("frozen freshness plan does not identify the immutable source dump")
    if actual_dump_sha.lower() != manifest.dump_identity.dump_sha256.lower():
        raise ValueError("dump SHA-256 does not match the frozen freshness plan")
    settings = load_settings()
    engine = create_engine(settings.database.url)
    try:
        with engine.connect() as connection:
            verify_manifest_identity(
                connection,
                recovery,
                dump_sha256=actual_dump_sha,
                mapping_version=settings.candles.version,
                schema=settings.candles.schema,
                table=settings.candles.table,
            )
        with engine.begin() as connection:
            connection.execute(text(candle_recovery_migration_sql()))
        report = run_freshness_sync(
            engine,
            manifest,
            compatibility,
            BinanceSpotSource(args.cache_dir),
        )
    finally:
        engine.dispose()
    paths = write_freshness_artifacts(manifest, report, args.output_dir)
    print(
        _json(
            {
                "manifest": str(paths.manifest),
                "report": str(paths.report),
                "report_sha256": report.sha256(),
                **report.to_dict(),
            }
        )
    )
    return freshness_exit_code(report)


def _report(args: argparse.Namespace) -> int:
    report = (
        read_latest_freshness_report(args.output_dir)
        if args.report is None
        else read_freshness_report(args.report)
    )
    print(_json(report.to_dict()))
    return freshness_exit_code(report)


def _health(args: argparse.Namespace) -> int:
    report = read_latest_freshness_report(args.output_dir)
    healthy = {FreshnessRunStatus.UP_TO_DATE, FreshnessRunStatus.RECOVERED}
    current = sum(item.status in healthy for item in report.symbols)
    payload = {
        "as_of": report.as_of,
        "current": current == len(report.symbols),
        "missing_minutes": report.after_missing_minutes,
        "report_sha256": report.sha256(),
        "symbols_current": current,
        "symbols_total": len(report.symbols),
    }
    print(_json(payload))
    return freshness_exit_code(report)


def _snapshot(args: argparse.Namespace) -> int:
    report = read_freshness_report(args.report)
    compatibility = _read_reviewed_compatibility(
        args.compatibility,
        args.compatibility_sha256,
    )
    if compatibility.sha256() != report.compatibility_manifest_sha256:
        raise ValueError("freshness report does not match the reviewed compatibility artifact")
    if compatibility.source_identity.dump_sha256.lower() != report.dump_sha256:
        raise ValueError("freshness report and compatibility artifact identify different dumps")
    if report.dump_sha256 != EXPECTED_DUMP_SHA256:
        raise ValueError("freshness report does not identify the immutable source dump")
    actual_dump_sha256 = sha256_file(args.dump_path).lower()
    if actual_dump_sha256 != report.dump_sha256:
        raise ValueError("dump SHA-256 does not match the freshness report")
    policy = (
        SnapshotPublicationPolicy.ALLOW_PROVENANCE_BLOCKED
        if args.allow_provenance_blocked
        else SnapshotPublicationPolicy.REQUIRE_HEALTHY
    )
    settings = load_settings()
    engine = create_engine(settings.database.url)
    try:
        with engine.connect() as connection:
            verify_manifest_identity(
                connection,
                compatibility,
                dump_sha256=actual_dump_sha256,
                mapping_version=settings.candles.version,
                schema=settings.candles.schema,
                table=settings.candles.table,
            )
        manifest = publish_freshness_snapshot(
            engine,
            report,
            output_root=args.output_root,
            dataset_version=args.dataset_version,
            config_version=args.config_version,
            code_commit=args.code_commit,
            policy=policy,
            batch_size=args.batch_size,
            settings=settings,
        )
    finally:
        engine.dispose()
    print(
        _json(
            {
                "dataset_version": manifest.identity.dataset_version,
                "freshness_report_sha256": report.sha256(),
                "manifest": str(
                    args.output_root
                    / f"dataset_version={manifest.identity.dataset_version}"
                    / "manifest.json"
                ),
                "row_count": manifest.row_count,
                "snapshot_sha256": manifest.snapshot_sha256,
            }
        )
    )
    return 0


def _read_reviewed_compatibility(path: Path, expected_sha256: str) -> RecoveryManifest:
    normalized = expected_sha256.lower()
    if len(normalized) != 64 or any(
        character not in "0123456789abcdef" for character in normalized
    ):
        raise ValueError("reviewed compatibility SHA-256 is invalid")
    compatibility = read_manifest(path)
    if compatibility.sha256() != normalized:
        raise ValueError("reviewed compatibility SHA-256 does not match the supplied artifact")
    return compatibility


def _write_plan_artifact(manifest: FreshnessManifest, directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    stamp = datetime.fromisoformat(manifest.as_of.replace("Z", "+00:00")).strftime("%Y%m%dT%H%M%SZ")
    path = directory / f"{stamp}-{manifest.sha256()[:12]}.plan.json"
    if path.exists():
        if read_freshness_manifest(path) != manifest:
            raise ValueError("immutable freshness plan path contains different content")
        return path
    write_freshness_manifest(manifest, path)
    return path


def _plan_payload(manifest: FreshnessManifest) -> dict[str, int]:
    return {
        "symbols": len(manifest.symbols),
        "missing_minutes": sum(item.canonical_state.missing_minutes for item in manifest.symbols),
        "eligible_minutes": sum(
            gap.expected_minutes for item in manifest.symbols for gap in item.eligible_ranges
        ),
    }


def _planning_exit_code(manifest: FreshnessManifest) -> int:
    return (
        0
        if all(item.status is FreshnessPlanningStatus.UP_TO_DATE for item in manifest.symbols)
        else 2
    )


def _add_compatibility_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--compatibility", type=Path, required=True)
    parser.add_argument("--compatibility-sha256", required=True)


def _json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


if __name__ == "__main__":
    raise SystemExit(main())
