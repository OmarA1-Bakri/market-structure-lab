"""CLI for non-mutating Phase 5 V2 scoped-source discovery."""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from pathlib import Path

from market_structure_lab.data.validation_source_v2 import (
    discover_scoped_source_v2,
    load_v2_boundary_publications,
    verify_scoped_source_availability_v2,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--coverage-config", type=Path, required=True)
    parser.add_argument("--split-config", type=Path, required=True)
    parser.add_argument("--boundary-publication", type=Path, required=True)
    parser.add_argument("--audit-ledger-root", type=Path, required=True)
    parser.add_argument("--candidate-root", type=Path, required=True)
    parser.add_argument("--network", required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    return parser


def run(args: argparse.Namespace) -> int:
    if args.network != "disabled":
        raise ValueError("scoped-source discovery requires network disabled")
    _, _, boundary = load_v2_boundary_publications(
        coverage_path=args.coverage_config,
        split_path=args.split_config,
        boundary_path=args.boundary_publication,
    )
    availability = discover_scoped_source_v2(
        boundary=boundary,
        candidate_root=args.candidate_root,
        audit_ledger_root=args.audit_ledger_root,
        output_root=args.output_root,
    )
    verify_scoped_source_availability_v2(
        availability,
        boundary=boundary,
        publication_root=args.output_root,
    )
    print(
        f"{availability.status.value} "
        f"{availability.availability_sha256} rows={availability.rows_read} final_rows=0"
    )
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    return run(build_parser().parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
