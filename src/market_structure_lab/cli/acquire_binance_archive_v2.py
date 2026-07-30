"""Acquire only a preregistered bounded Binance archive manifest."""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from pathlib import Path

from market_structure_lab.cli.freeze_binance_archive_requests_v2 import (
    locate_boundary_parent_publications_v2,
)
from market_structure_lab.data.binance_archive_v2 import (
    acquire_binance_archives_v2,
    load_binance_archive_request_manifest_for_acquisition_v2,
)
from market_structure_lab.data.validation_source_v2 import (
    load_v2_boundary_publications,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--boundary-publication", type=Path, required=True)
    parser.add_argument("--audit-ledger-root", type=Path, required=True)
    parser.add_argument("--request-manifest", type=Path, required=True)
    parser.add_argument("--cache-root", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    return parser


def run(args: argparse.Namespace) -> int:
    coverage_path, split_path = locate_boundary_parent_publications_v2(
        args.boundary_publication
    )
    _, _, boundary = load_v2_boundary_publications(
        coverage_path=coverage_path,
        split_path=split_path,
        boundary_path=args.boundary_publication,
    )
    manifest = load_binance_archive_request_manifest_for_acquisition_v2(
        publication_path=args.request_manifest,
        boundary=boundary,
    )
    result = acquire_binance_archives_v2(
        boundary=boundary,
        manifest=manifest,
        manifest_path=args.request_manifest,
        audit_ledger_root=args.audit_ledger_root,
        cache_root=args.cache_root,
        output_root=args.output_root,
    )
    print(
        f"{result.status} {result.publication_sha256} "
        f"objects={result.object_count} final_rows=0"
    )
    return 0
def main(argv: Sequence[str] | None = None) -> int:
    return run(build_parser().parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
