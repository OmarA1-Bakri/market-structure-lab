"""CLI for offline development-only verified 1h/4h aggregate publication."""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from pathlib import Path

from market_structure_lab.data.aggregate_publication_v2 import (
    AggregatePublicationBudgetV2,
    publish_validation_aggregates_v2,
)
from market_structure_lab.data.validation_source_v2 import (
    load_scoped_source_availability_v2,
    load_validation_source_publication_v2,
    load_v2_boundary_publications,
)
from market_structure_lab.research.validation_v2_models import SourcePublicationIdentityV2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--coverage-config", type=Path, required=True)
    parser.add_argument("--split-config", type=Path, required=True)
    parser.add_argument("--boundary-publication", type=Path, required=True)
    parser.add_argument("--availability-root", type=Path, required=True)
    parser.add_argument("--minute-publication-root", type=Path, required=True)
    parser.add_argument("--minute-audit-root", type=Path, required=True)
    parser.add_argument("--expected-minute-identity", required=True)
    parser.add_argument("--network", required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--max-source-rows", type=int, default=50_000_000)
    parser.add_argument("--max-source-bytes", type=int, default=64 * 1024 * 1024 * 1024)
    parser.add_argument("--max-parent-partitions", type=int, default=99_998)
    parser.add_argument("--max-source-rows-per-chunk", type=int, default=100_000)
    parser.add_argument("--max-members", type=int, default=100_000)
    parser.add_argument("--max-aggregate-rows", type=int, default=2_000_000)
    parser.add_argument("--max-rows-per-partition", type=int, default=100_000)
    parser.add_argument("--max-output-bytes", type=int, default=16 * 1024 * 1024 * 1024)
    parser.add_argument("--max-output-files", type=int, default=99_998)
    return parser


def run(args: argparse.Namespace) -> int:
    if args.network != "disabled":
        raise ValueError("aggregate publication requires network disabled")
    coverage, split, boundary = load_v2_boundary_publications(
        coverage_path=args.coverage_config,
        split_path=args.split_config,
        boundary_path=args.boundary_publication,
    )
    availability = load_scoped_source_availability_v2(
        publication_root=args.availability_root,
        boundary=boundary,
    )
    minute = load_validation_source_publication_v2(
        publication_root=args.minute_publication_root,
        audit_ledger_root=args.minute_audit_root,
        coverage=coverage,
        split=split,
        boundary=boundary,
        availability=availability,
    )
    expected = SourcePublicationIdentityV2(args.expected_minute_identity)
    if minute.source_publication_identity.value != expected.value:
        raise ValueError("minute publication differs from expected frozen identity")
    publication = publish_validation_aggregates_v2(
        minute_publication=minute,
        coverage=coverage,
        split=split,
        boundary=boundary,
        availability=availability,
        output_root=args.output_root,
        budget=AggregatePublicationBudgetV2(
            max_source_rows=args.max_source_rows,
            max_source_bytes=args.max_source_bytes,
            max_parent_partitions=args.max_parent_partitions,
            max_source_rows_per_chunk=args.max_source_rows_per_chunk,
            max_members=args.max_members,
            max_aggregate_rows=args.max_aggregate_rows,
            max_rows_per_partition=args.max_rows_per_partition,
            max_output_bytes=args.max_output_bytes,
            max_output_files=args.max_output_files,
        ),
    )
    print(
        f"{publication.status.value} {publication.aggregate_identity.value} "
        f"rows={publication.row_count} final_rows={publication.final_rows}"
    )
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    return run(build_parser().parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
