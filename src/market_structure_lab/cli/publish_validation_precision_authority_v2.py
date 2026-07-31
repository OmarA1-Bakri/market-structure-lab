"""Publish offline Phase 5 historical price-precision authority."""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from pathlib import Path

from market_structure_lab.data.aggregate_publication_v2 import (
    load_validation_aggregate_publication_v2,
)
from market_structure_lab.data.validation_precision_authority_v2 import (
    PrecisionAuthorityRequestV2,
    PrecisionSourceKindV2,
    publish_validation_precision_authority_v2,
)
from market_structure_lab.data.validation_source_v2 import (
    load_scoped_source_availability_v2,
    load_validation_source_publication_v2,
    load_v2_boundary_publications,
)
from market_structure_lab.research.validation_v2_models import (
    AggregatePublicationIdentityV2,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--coverage-config", type=Path, required=True)
    parser.add_argument("--split-config", type=Path, required=True)
    parser.add_argument("--boundary-publication", type=Path, required=True)
    parser.add_argument("--availability-root", type=Path, required=True)
    parser.add_argument("--minute-publication-root", type=Path, required=True)
    parser.add_argument("--minute-audit-root", type=Path, required=True)
    parser.add_argument("--aggregate-publication-root", type=Path, required=True)
    parser.add_argument("--expected-aggregate-identity", required=True)
    parser.add_argument("--venue", required=True)
    parser.add_argument("--query-source", required=True)
    parser.add_argument(
        "--source-kind", choices=tuple(item.value for item in PrecisionSourceKindV2), required=True
    )
    parser.add_argument("--retrieval-identity-sha256", required=True)
    parser.add_argument("--checkpoint-identity-sha256", required=True)
    parser.add_argument("--limitation")
    parser.add_argument("--authority-source", type=Path)
    parser.add_argument("--verifier-evidence", type=Path)
    parser.add_argument("--network", required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    return parser


def run(args: argparse.Namespace) -> int:
    if args.network != "disabled":
        raise ValueError("precision authority publication requires network disabled")
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
    request = PrecisionAuthorityRequestV2.from_boundary(
        boundary=boundary,
        venue=args.venue,
        query_source=args.query_source,
        source_kind=PrecisionSourceKindV2(args.source_kind),
        retrieval_identity_sha256=args.retrieval_identity_sha256,
        checkpoint_identity_sha256=args.checkpoint_identity_sha256,
        limitation=args.limitation,
    )
    publication = publish_validation_precision_authority_v2(
        request=request,
        coverage=coverage,
        split=split,
        boundary=boundary,
        availability=availability,
        minute_publication=minute,
        aggregate_publication=aggregate,
        authority_source_path=args.authority_source,
        verifier_evidence_path=args.verifier_evidence,
        output_root=args.output_root,
    )
    print(
        f"{publication.status.value} {publication.precision_authority_identity.value} "
        "signals=0 outcomes=0 final_rows=0"
    )
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    return run(build_parser().parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
