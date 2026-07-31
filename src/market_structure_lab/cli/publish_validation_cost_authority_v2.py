"""CLI for offline authenticated Phase 5 V2 cost completeness publication."""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from pathlib import Path

from market_structure_lab.data.aggregate_publication_v2 import (
    load_validation_aggregate_publication_v2,
)
from market_structure_lab.data.binance_archive_v2 import (
    load_binance_archive_acquisition_v2,
    load_binance_archive_request_manifest_v2,
)
from market_structure_lab.data.validation_source_v2 import (
    load_scoped_source_availability_v2,
    load_validation_source_publication_v2,
    load_v2_boundary_publications,
)
from market_structure_lab.research.validation_v2_costs import (
    publish_validation_cost_authority_v2,
)
from market_structure_lab.research.validation_v2_models import (
    AggregatePublicationIdentityV2,
    SourcePublicationIdentityV2,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--coverage-config", type=Path, required=True)
    parser.add_argument("--split-config", type=Path, required=True)
    parser.add_argument("--boundary-publication", type=Path, required=True)
    parser.add_argument("--availability-root", type=Path, required=True)
    parser.add_argument("--minute-publication-root", type=Path, required=True)
    parser.add_argument("--minute-audit-root", type=Path, required=True)
    parser.add_argument("--expected-minute-identity", required=True)
    parser.add_argument("--aggregate-publication-root", type=Path, required=True)
    parser.add_argument("--expected-aggregate-identity", required=True)
    parser.add_argument("--archive-manifest", type=Path, required=True)
    parser.add_argument("--archive-publication-root", type=Path, required=True)
    parser.add_argument("--archive-cache-root", type=Path, required=True)
    parser.add_argument("--network", required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    return parser


def run(args: argparse.Namespace) -> int:
    if args.network != "disabled":
        raise ValueError("cost authority publication requires network disabled")
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
    expected_minute = SourcePublicationIdentityV2(args.expected_minute_identity)
    if minute.source_publication_identity.value != expected_minute.value:
        raise ValueError("minute publication differs from expected frozen identity")
    aggregate = load_validation_aggregate_publication_v2(
        publication_root=args.aggregate_publication_root,
        expected_aggregate_identity=AggregatePublicationIdentityV2(
            args.expected_aggregate_identity
        ),
        minute_publication=minute,
        coverage=coverage,
        split=split,
        boundary=boundary,
        availability=availability,
    )
    manifest = load_binance_archive_request_manifest_v2(
        publication_path=args.archive_manifest,
        boundary=boundary,
        source_availability=availability,
    )
    acquisition = load_binance_archive_acquisition_v2(
        publication_root=args.archive_publication_root,
        cache_root=args.archive_cache_root,
        boundary=boundary,
        manifest=manifest,
        manifest_path=args.archive_manifest,
    )
    authority = publish_validation_cost_authority_v2(
        source_publication=minute,
        aggregate_publication=aggregate,
        coverage=coverage,
        split=split,
        boundary=boundary,
        availability=availability,
        archive_manifest=manifest,
        archive_manifest_path=args.archive_manifest,
        archive_acquisition=acquisition,
        output_root=args.output_root,
    )
    print(
        f"{authority.evaluation_status.value} {authority.conclusion.value} "
        f"{authority.cost_identity.value}"
    )
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    return run(build_parser().parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
