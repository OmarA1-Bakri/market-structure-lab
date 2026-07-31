"""CLI for offline development-only canonical minute source publication."""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from pathlib import Path

from market_structure_lab.data.validation_source_v2 import (
    load_scoped_source_availability_v2,
    load_v2_boundary_publications,
    publish_validation_source_v2,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--coverage-config", type=Path, required=True)
    parser.add_argument("--split-config", type=Path, required=True)
    parser.add_argument("--boundary-publication", type=Path, required=True)
    parser.add_argument("--availability-root", type=Path, required=True)
    parser.add_argument("--audit-ledger-root", type=Path, required=True)
    parser.add_argument("--network", required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--max-rows-per-partition", type=int, default=100_000)
    parser.add_argument("--max-total-rows", type=int, default=50_000_000)
    parser.add_argument(
        "--max-total-bytes", type=int, default=64 * 1024 * 1024 * 1024
    )
    parser.add_argument("--max-partitions", type=int, default=99_998)
    return parser


def run(args: argparse.Namespace) -> int:
    if args.network != "disabled":
        raise ValueError("validation source publication requires network disabled")
    coverage, split, boundary = load_v2_boundary_publications(
        coverage_path=args.coverage_config,
        split_path=args.split_config,
        boundary_path=args.boundary_publication,
    )
    availability = load_scoped_source_availability_v2(
        publication_root=args.availability_root,
        boundary=boundary,
    )
    publication = publish_validation_source_v2(
        coverage=coverage,
        split=split,
        boundary=boundary,
        availability=availability,
        source_capability=None,
        publication_root=args.output_root,
        audit_ledger_root=args.audit_ledger_root,
        max_rows_per_partition=args.max_rows_per_partition,
        max_total_rows=args.max_total_rows,
        max_total_bytes=args.max_total_bytes,
        max_partitions=args.max_partitions,
    )
    print(
        f"{publication.status.value} "
        f"{publication.source_publication_identity.value} "
        f"rows={publication.row_count} final_rows=0"
    )
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    return run(build_parser().parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
