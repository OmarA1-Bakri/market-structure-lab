"""Operator CLI for row-level Binance candle reconciliation."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

import polars as pl
from sqlalchemy import create_engine, text

from market_structure_lab.core.config import load_settings
from market_structure_lab.data.gaps import read_manifest
from market_structure_lab.data.migrations import (
    candle_reconciliation_migration_sql,
    candle_recovery_migration_sql,
    reconciliation_migration_lock_sql,
)
from market_structure_lab.data.reconciliation import (
    ReconciliationRepository,
    TradingEnvelope,
    VerifiedCoverageInterval,
    execute_work_unit,
    freeze_reconciliation_run,
    monthly_work_units,
    read_reconciliation_run,
    read_work_unit_manifest,
    write_reconciliation_run,
)
from market_structure_lab.data.sources.base import SourceError
from market_structure_lab.data.sources.binance import BinanceSpotSource

MINUTE_MS = 60_000
CANONICAL_HISTORY_START = datetime(2018, 1, 1, tzinfo=UTC)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Audit immutable candles against official Binance Spot observations"
    )
    commands = parser.add_subparsers(dest="operation", required=True)

    plan = commands.add_parser("plan", help="freeze a deterministic RR-* reconciliation run")
    plan.add_argument("--run-id", required=True)
    plan.add_argument("--compatibility", type=Path, required=True)
    plan.add_argument("--compatibility-sha256", required=True)
    plan.add_argument("--start")
    plan.add_argument("--end")
    plan.add_argument(
        "--full-envelopes",
        action="store_true",
        help="start each symbol at its first immutable observation and end at cutoff",
    )
    plan.add_argument("--cutoff", required=True)
    plan.add_argument("--symbol", action="append", default=[])
    plan.add_argument("--code-commit", required=True)
    plan.add_argument("--uv-lock-sha256", required=True)
    plan.add_argument("--output", type=Path, required=True)

    run = commands.add_parser("run", help="execute or resume frozen reconciliation work units")
    run.add_argument("--run", type=Path, required=True)
    run.add_argument("--output-root", type=Path, required=True)
    run.add_argument("--cache-dir", type=Path, default=Path("data/cache/binance"))
    run.add_argument("--dump-path", type=Path, default=Path("data/dumps/callscore.dump"))
    run.add_argument("--work-unit", action="append", default=[])
    run.add_argument("--batch-size", type=int, default=10_000)
    run.add_argument("--max-rows-per-part", type=int, default=100_000)
    run.add_argument("--apply", action="store_true")

    promote = commands.add_parser(
        "promote", help="promote verified coverage and replacements atomically"
    )
    promote.add_argument("--run", type=Path, required=True)
    promote.add_argument("--output-root", type=Path, required=True)
    promote.add_argument("--apply", action="store_true")

    report = commands.add_parser("report", help="report checksum-verified reconciliation evidence")
    report.add_argument("--run", type=Path, required=True)
    report.add_argument("--output-root", type=Path, required=True)

    eligible = commands.add_parser(
        "eligible", help="emit verified contiguous intervals eligible for snapshots"
    )
    eligible.add_argument("--run", type=Path, required=True)
    eligible.add_argument("--output-root", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.operation == "plan":
            return _plan(args)
        if args.operation == "run":
            return _run(args)
        if args.operation == "promote":
            return _promote(args)
        if args.operation == "report":
            return _report(args)
        return _eligible(args)
    except (OSError, ValueError, RuntimeError, SourceError) as error:
        print(str(error), file=sys.stderr)
        return 1


def _plan(args: argparse.Namespace) -> int:
    compatibility = read_manifest(args.compatibility)
    expected_sha = args.compatibility_sha256.lower()
    if compatibility.sha256() != expected_sha:
        raise ValueError("compatibility artifact checksum does not match the supplied SHA-256")
    available = {item.symbol for item in compatibility.envelopes}
    selected = set(args.symbol) if args.symbol else available
    unknown = selected - available
    if unknown:
        raise ValueError(f"symbols are absent from compatibility evidence: {', '.join(sorted(unknown))}")
    cutoff = _parse_minute(args.cutoff, "cutoff")
    cutoff_ms = int(cutoff.timestamp() * 1_000)
    history_start_ms = int(CANONICAL_HISTORY_START.timestamp() * 1_000)
    if cutoff <= CANONICAL_HISTORY_START:
        raise ValueError("cutoff must be after the canonical history start")
    if args.full_envelopes:
        if args.start is not None or args.end is not None:
            raise ValueError("full-envelope planning cannot also specify start or end")
        evidence = {item.symbol: item for item in compatibility.envelopes}
        envelopes = tuple(
            TradingEnvelope(
                symbol,
                evidence[symbol].timeframe,
                max(evidence[symbol].first_open_time_ms, history_start_ms),
                cutoff_ms,
            )
            for symbol in sorted(selected)
        )
        if any(item.start_ms >= item.end_ms for item in envelopes):
            raise ValueError("a selected evidence envelope starts at or after the cutoff")
    else:
        if args.start is None or args.end is None:
            raise ValueError("fixed-window planning requires both start and end")
        start = _parse_minute(args.start, "start")
        end = _parse_minute(args.end, "end")
        if start < CANONICAL_HISTORY_START:
            raise ValueError("canonical history starts at 2018-01-01T00:00:00Z")
        if not start < end <= cutoff:
            raise ValueError("reconciliation requires start < end <= cutoff")
        start_ms = int(start.timestamp() * 1_000)
        end_ms = int(end.timestamp() * 1_000)
        envelopes = tuple(
            TradingEnvelope(symbol, "1m", start_ms, end_ms) for symbol in sorted(selected)
        )
    units = tuple(
        unit
        for envelope in envelopes
        for unit in monthly_work_units(
            symbol=envelope.symbol,
            timeframe=envelope.timeframe,
            start_ms=envelope.start_ms,
            end_ms=envelope.end_ms,
        )
    )
    run = freeze_reconciliation_run(
        run_id=args.run_id,
        cutoff=cutoff,
        dump_sha256=compatibility.source_identity.dump_sha256,
        source_row_count=compatibility.source_identity.source_row_count,
        mapping_version=compatibility.source_identity.mapping_version,
        candidate_venue=compatibility.candidate_venue,
        market_type=compatibility.market_type,
        source_revision="binance-public-data-v1",
        algorithm_version="row-reconciliation-v3",
        code_commit=args.code_commit,
        uv_lock_sha256=args.uv_lock_sha256,
        envelopes=envelopes,
        work_units=units,
    )
    write_reconciliation_run(run, args.output)
    print(
        _json(
            {
                "manifest": str(args.output),
                "manifest_sha256": run.manifest_sha256,
                "run_id": run.run_id,
                "symbols": len(envelopes),
                "work_units": len(units),
            }
        )
    )
    return 0


def _run(args: argparse.Namespace) -> int:
    run = read_reconciliation_run(args.run)
    if _sha256_file(args.dump_path) != run.dump_sha256:
        raise ValueError("immutable dump checksum does not match the reconciliation run")
    selected = set(args.work_unit)
    unknown = selected - {item.work_unit_id for item in run.work_units}
    if unknown:
        raise ValueError(f"work units are absent from the run: {', '.join(sorted(unknown))}")
    units = tuple(item for item in run.work_units if not selected or item.work_unit_id in selected)
    settings = load_settings()
    engine = create_engine(settings.database.url)
    summaries: list[dict[str, object]] = []
    try:
        if args.apply:
            with engine.begin() as connection:
                connection.execute(text(reconciliation_migration_lock_sql()))
                connection.execute(text(candle_recovery_migration_sql()))
                connection.execute(text(candle_reconciliation_migration_sql()))
                _verify_source_identity(connection, run)
                ReconciliationRepository(connection).register_run(run)
        source = BinanceSpotSource(args.cache_dir)
        for unit in units:
            with engine.connect() as connection:
                execution = execute_work_unit(
                    connection,
                    source,
                    run=run,
                    work_unit=unit,
                    output_root=args.output_root,
                    batch_size=args.batch_size,
                    max_rows_per_part=args.max_rows_per_part,
                )
            inserted = 0
            if args.apply:
                with engine.begin() as connection:
                    repository = ReconciliationRepository(connection)
                    repository.register_run(run)
                    inserted = repository.publish_work_unit(
                        execution.manifest,
                        execution.replacements,
                    )
            summaries.append(
                {
                    "inserted_replacements": inserted,
                    "manifest_sha256": execution.manifest.manifest_sha256,
                    "replacement_rows": execution.manifest.replacement_row_count,
                    "status": execution.manifest.status,
                    "work_unit_id": unit.work_unit_id,
                }
            )
    finally:
        engine.dispose()
    print(_json({"applied": args.apply, "run_id": run.run_id, "work_units": summaries}))
    return 0


def _promote(args: argparse.Namespace) -> int:
    run = read_reconciliation_run(args.run)
    manifests = _load_work_unit_manifests(run, args.output_root)
    coverage = _coverage_for_run(run, manifests, args.output_root)
    if not args.apply:
        print(
            _json(
                {
                    "applied": False,
                    "coverage_intervals": len(coverage),
                    "run_id": run.run_id,
                    "work_units": len(manifests),
                }
            )
        )
        return 0
    settings = load_settings()
    engine = create_engine(settings.database.url)
    try:
        with engine.begin() as connection:
            connection.execute(text(candle_reconciliation_migration_sql()))
            repository = ReconciliationRepository(connection)
            repository.register_run(run)
            promotion = repository.promote(run, manifests, coverage)
    finally:
        engine.dispose()
    print(
        _json(
            {
                "applied": True,
                "canonical_logical_sha256": promotion.canonical_logical_sha256,
                "promoted_at": promotion.promoted_at,
                "replacement_logical_sha256": promotion.replacement_logical_sha256,
                "run_id": promotion.run_id,
            }
        )
    )
    return 0


def _report(args: argparse.Namespace) -> int:
    run = read_reconciliation_run(args.run)
    manifests = _load_work_unit_manifests(run, args.output_root)
    classifications: Counter[str] = Counter()
    differing: Counter[str] = Counter()
    for manifest in manifests:
        classifications.update(dict(manifest.classification_counts))
        differing.update(dict(manifest.differing_field_counts))
    payload = {
        "classification_counts": dict(sorted(classifications.items())),
        "differing_field_counts": dict(sorted(differing.items())),
        "manifest_sha256": run.manifest_sha256,
        "replacement_rows": sum(item.replacement_row_count for item in manifests),
        "row_count": sum(item.row_count for item in manifests),
        "run_id": run.run_id,
        "statuses": dict(sorted(Counter(item.status for item in manifests).items())),
        "work_units": len(manifests),
    }
    print(_json(payload))
    return 0


def _eligible(args: argparse.Namespace) -> int:
    run = read_reconciliation_run(args.run)
    manifests = _load_work_unit_manifests(run, args.output_root)
    intervals = _merge_coverage(_coverage_for_run(run, manifests, args.output_root))
    print(
        _json(
            {
                "intervals": [
                    {
                        "end": _iso_ms(item.end_ms),
                        "start": _iso_ms(item.start_ms),
                        "symbol": item.symbol,
                        "timeframe": item.timeframe,
                    }
                    for item in intervals
                ],
                "run_id": run.run_id,
            }
        )
    )
    return 0


def _load_work_unit_manifests(run, output_root: Path):
    found = {
        manifest.work_unit_id: manifest
        for path in output_root.glob(f"run_id={run.run_id}/**/manifest.json")
        for manifest in (read_work_unit_manifest(path),)
    }
    expected = {item.work_unit_id for item in run.work_units}
    if set(found) != expected:
        missing = sorted(expected - set(found))
        extra = sorted(set(found) - expected)
        raise ValueError(f"work-unit evidence is incomplete; missing={missing}, extra={extra}")
    return tuple(found[item.work_unit_id] for item in run.work_units)


def _coverage_for_run(run, manifests, output_root: Path):
    by_id = {item.work_unit_id: item for item in manifests}
    intervals: list[VerifiedCoverageInterval] = []
    for unit in run.work_units:
        manifest = by_id[unit.work_unit_id]
        publication = output_root / manifest.publication_path
        start: int | None = None
        for part in manifest.parts:
            frame = pl.read_parquet(
                publication / part.path,
                columns=["open_time_ms", "classification"],
            )
            for timestamp, classification in frame.iter_rows():
                timestamp = int(timestamp)
                if classification == "source_unavailable":
                    if start is not None:
                        intervals.append(
                            VerifiedCoverageInterval(unit.symbol, unit.timeframe, start, timestamp)
                        )
                        start = None
                elif start is None:
                    start = timestamp
        if start is not None:
            intervals.append(
                VerifiedCoverageInterval(unit.symbol, unit.timeframe, start, unit.end_ms)
            )
    return tuple(intervals)


def _merge_coverage(intervals):
    merged: list[VerifiedCoverageInterval] = []
    for item in sorted(intervals, key=lambda value: (value.symbol, value.timeframe, value.start_ms)):
        if (
            merged
            and (merged[-1].symbol, merged[-1].timeframe) == (item.symbol, item.timeframe)
            and merged[-1].end_ms == item.start_ms
        ):
            prior = merged.pop()
            merged.append(
                VerifiedCoverageInterval(
                    item.symbol,
                    item.timeframe,
                    prior.start_ms,
                    item.end_ms,
                )
            )
        else:
            merged.append(item)
    return tuple(merged)


def _verify_source_identity(connection, run) -> None:
    count = int(connection.execute(text("SELECT count(*) FROM market_data.candles")).scalar_one())
    if count != run.source_row_count:
        raise ValueError("immutable source row count differs from the reconciliation run")


def _parse_minute(value: str, name: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError(f"{name} must be an ISO-8601 UTC timestamp") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")
    parsed = parsed.astimezone(UTC)
    if parsed.second or parsed.microsecond:
        raise ValueError(f"{name} must be minute-aligned")
    return parsed


def _iso_ms(value: int) -> str:
    return datetime.fromtimestamp(value / 1_000, tz=UTC).isoformat().replace("+00:00", "Z")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


if __name__ == "__main__":
    raise SystemExit(main())
