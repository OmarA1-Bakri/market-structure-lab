"""Command-line interface for real database inspection."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence

from market_structure_lab.core.config import load_settings
from market_structure_lab.data.inspection import (
    DatabaseInspectionError,
    InspectionMode,
    inspect_configured_database,
    render_human,
    render_json,
    write_json_report,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Inspect PostgreSQL schema and candle data quality"
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--quick", action="store_true", help="schema discovery and estimated counts")
    mode.add_argument("--full", action="store_true", help="exact counts and candle-quality metrics")
    parser.add_argument(
        "--json",
        metavar="PATH",
        help="write deterministic JSON to PATH, or use '-' to print JSON only",
    )
    parser.add_argument("--sample-limit", type=int, default=5)
    parser.add_argument("--large-gap-minutes", type=int, default=60)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    inspection_mode = InspectionMode.FULL if args.full else InspectionMode.QUICK
    try:
        report = inspect_configured_database(
            load_settings(),
            mode=inspection_mode,
            sample_limit=args.sample_limit,
            large_gap_minutes=args.large_gap_minutes,
        )
    except (DatabaseInspectionError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 1

    if args.json == "-":
        print(render_json(report), end="")
    else:
        print(render_human(report), end="")
        if args.json:
            write_json_report(report, args.json)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
