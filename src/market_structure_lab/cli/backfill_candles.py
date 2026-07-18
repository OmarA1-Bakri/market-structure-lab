"""Thin plan/fetch/validate/report CLI for frozen candle recovery."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.exc import SQLAlchemyError

from market_structure_lab.cli.errors import database_error_message
from market_structure_lab.core.config import load_settings
from market_structure_lab.data.gaps import (
    ProvenanceState,
    RecoveryManifest,
    build_manifest,
    read_manifest,
    verify_manifest_identity,
    with_provenance_states,
    write_manifest,
)
from market_structure_lab.data.migrations import SchemaPreparationBusyError, prepare_recovery_schema
from market_structure_lab.data.recovery import (
    build_recovery_report,
    coalesce_requests,
    compare_source_compatibility,
    fetch_source_samples,
    load_compatibility_dump_samples,
    run_recovery,
)
from market_structure_lab.data.sources.base import FetchRequest, SourceError
from market_structure_lab.data.sources.binance import BinanceSpotSource

EXPECTED_DUMP_SHA256 = "1b6bcb39af41048b53729e9b094f0229163eb6ff6af9563adb666c96f5fd4da4"
REVIEWED_BASELINE = {
    "symbols": 25,
    "gap_ranges": 19_192,
    "missing_minutes": 23_372_460,
    "source_rows": 35_748_117,
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Recover real missing Binance Spot 1m candles")
    commands = parser.add_subparsers(dest="operation", required=True)

    plan = commands.add_parser("plan", help="inspect gaps and freeze a recovery manifest")
    plan.add_argument("--as-of", required=True, help="fixed UTC recovery cutoff")
    plan.add_argument("--output", type=Path, required=True)
    plan.add_argument("--dump-sha256", default=EXPECTED_DUMP_SHA256)

    validate = commands.add_parser("validate", help="validate source compatibility per symbol")
    validate.add_argument("--manifest", type=Path, required=True)
    validate.add_argument("--output", type=Path, required=True)
    validate.add_argument("--cache-dir", type=Path, default=Path("data/cache/binance"))
    validate.add_argument("--symbol", action="append", default=[])

    fetch = commands.add_parser("fetch", help="recover a validated frozen manifest")
    fetch.add_argument("--manifest", type=Path, required=True)
    fetch.add_argument("--cache-dir", type=Path, default=Path("data/cache/binance"))
    fetch.add_argument("--dump-path", type=Path, default=Path("data/dumps/callscore.dump"))
    fetch.add_argument(
        "--apply",
        action="store_true",
        help="apply the versioned migration and publish validated supplements",
    )

    report = commands.add_parser("report", help="report recovery ledger classifications")
    report.add_argument("--manifest", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.operation == "plan":
            return _plan(args)
        if args.operation == "validate":
            return _validate(args)
        if args.operation == "fetch":
            return _fetch(args)
        return _report(args)
    except SQLAlchemyError as error:
        print(database_error_message(error), file=sys.stderr)
        return 1
    except SchemaPreparationBusyError:
        print("candle schema preparation is busy", file=sys.stderr)
        return 1
    except (OSError, ValueError, SourceError) as error:
        print(str(error), file=sys.stderr)
        return 1
    except RuntimeError:
        print("candle recovery operation failed", file=sys.stderr)
        return 1


def _plan(args: argparse.Namespace) -> int:
    settings = load_settings()
    as_of = datetime.fromisoformat(args.as_of.replace("Z", "+00:00"))
    engine = create_engine(settings.database.url)
    try:
        with engine.connect() as connection:
            manifest = build_manifest(
                connection,
                dump_sha256=args.dump_sha256,
                mapping_version=settings.candles.version,
                as_of=as_of,
                schema=settings.candles.schema,
                table=settings.candles.table,
                symbol_column=settings.candles.symbol_column,
                timeframe_column=settings.candles.timeframe_column,
                timestamp_column=settings.candles.timestamp_column,
            )
        write_manifest(manifest, args.output)
    finally:
        engine.dispose()
    baseline = compare_manifest_to_reviewed_baseline(manifest)
    print(
        _json(
            {
                "manifest": str(args.output),
                "sha256": manifest.sha256(),
                **_plan_counts(manifest),
                "reviewed_baseline": baseline,
            }
        )
    )
    return 0 if baseline["matches"] else 2


def _validate(args: argparse.Namespace) -> int:
    settings = load_settings()
    manifest = read_manifest(args.manifest)
    selected = set(args.symbol) if args.symbol else {item.symbol for item in manifest.envelopes}
    unknown = selected - {item.symbol for item in manifest.envelopes}
    if unknown:
        raise ValueError(f"symbols are absent from manifest: {', '.join(sorted(unknown))}")
    states = dict(manifest.provenance_validation)
    evidence: dict[str, object] = {}
    source = BinanceSpotSource(args.cache_dir)
    engine = create_engine(settings.database.url)
    try:
        with engine.connect() as connection:
            verify_manifest_identity(
                connection,
                manifest,
                dump_sha256=manifest.source_identity.dump_sha256,
                mapping_version=settings.candles.version,
                schema=settings.candles.schema,
                table=settings.candles.table,
            )
            for envelope in manifest.envelopes:
                if envelope.symbol not in selected:
                    continue
                dump_rows = load_compatibility_dump_samples(
                    connection,
                    symbol=envelope.symbol,
                    timeframe=envelope.timeframe,
                    total_rows=envelope.row_count,
                    gaps=manifest.gaps,
                )
                try:
                    source_rows = fetch_source_samples(source, dump_rows)
                    result = compare_source_compatibility(
                        dump_rows, source_rows, existing_rows=envelope.row_count
                    )
                    states[envelope.symbol] = (
                        ProvenanceState.COMPATIBLE
                        if result.compatible
                        else ProvenanceState.SOURCE_CONFLICT
                    )
                    evidence[envelope.symbol] = {
                        "required_samples": result.required_samples,
                        "compared_samples": result.compared_samples,
                        "limited_evidence": result.limited_evidence,
                        "source_contract": "binance_spot_rest_api_v3_klines_milliseconds",
                        "mismatches": list(result.mismatches),
                    }
                except SourceError as error:
                    states[envelope.symbol] = ProvenanceState.SOURCE_UNAVAILABLE
                    evidence[envelope.symbol] = {"error": str(error)}
    finally:
        engine.dispose()
    validated = with_provenance_states(manifest, states)
    write_manifest(validated, args.output)
    print(_json({"manifest": str(args.output), "sha256": validated.sha256(), "evidence": evidence}))
    return 0 if all(states[symbol] == ProvenanceState.COMPATIBLE for symbol in selected) else 2


def _fetch(args: argparse.Namespace) -> int:
    settings = load_settings()
    manifest = read_manifest(args.manifest)
    counts = _plan_counts(manifest)
    if not args.apply:
        print(_json({"dry_run": True, "manifest_sha256": manifest.sha256(), **counts}))
        return 0
    pending = sorted(
        symbol
        for symbol, state in manifest.provenance_validation.items()
        if state == ProvenanceState.PENDING
    )
    if pending:
        raise ValueError(f"source compatibility is pending for: {', '.join(pending)}")
    actual_dump_hash = _sha256_file(args.dump_path)
    if actual_dump_hash != manifest.source_identity.dump_sha256:
        raise ValueError("frozen manifest dump SHA-256 does not match the immutable source archive")
    engine = create_engine(settings.database.url)
    try:
        with engine.connect() as connection:
            verify_manifest_identity(
                connection,
                manifest,
                dump_sha256=actual_dump_hash,
                mapping_version=settings.candles.version,
                schema=settings.candles.schema,
                table=settings.candles.table,
            )
        with engine.begin() as connection:
            # Let SQLAlchemy compile percent operators for the active DB-API.
            # Sending this migration as raw psycopg SQL treats PostgreSQL's
            # modulo operator as an incomplete parameter placeholder.
            prepare_recovery_schema(connection)
        summary = run_recovery(engine, manifest, BinanceSpotSource(args.cache_dir))
    finally:
        engine.dispose()
    print(
        _json(
            {
                "run_id": str(summary.run_id),
                "manifest_sha256": summary.manifest_sha256,
                "gaps_total": summary.gaps_total,
                "gaps_completed": summary.gaps_completed,
                "inserted_rows": summary.inserted_rows,
                "logical_hash": summary.logical_hash,
            }
        )
    )
    return 0


def _report(args: argparse.Namespace) -> int:
    settings = load_settings()
    manifest = read_manifest(args.manifest)
    engine = create_engine(settings.database.url)
    try:
        with engine.connect() as connection:
            report = build_recovery_report(connection, manifest)
    finally:
        engine.dispose()
    print(_json(report))
    return 0


def _plan_counts(manifest: RecoveryManifest) -> dict[str, int]:
    gaps = manifest.gaps
    requests = coalesce_requests(
        FetchRequest(gap.symbol, gap.timeframe, gap.start_ms, gap.end_ms) for gap in gaps
    )
    return {
        "symbols": len(manifest.envelopes),
        "gap_ranges": len(gaps),
        "fetch_ranges": len(requests),
        "missing_minutes": sum(gap.expected_minutes for gap in gaps),
        "source_rows": manifest.source_identity.source_row_count,
    }


def compare_manifest_to_reviewed_baseline(manifest: RecoveryManifest) -> dict[str, object]:
    actual = _plan_counts(manifest)
    mismatches = {
        key: {"expected": expected, "actual": actual[key]}
        for key, expected in REVIEWED_BASELINE.items()
        if actual[key] != expected
    }
    return {
        "expected": REVIEWED_BASELINE,
        "matches": not mismatches,
        "mismatches": mismatches,
    }


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


if __name__ == "__main__":
    raise SystemExit(main())
