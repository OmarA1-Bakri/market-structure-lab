"""Freeze a development-only Binance archive request manifest without network I/O."""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from pathlib import Path

from market_structure_lab.data.binance_archive_v2 import (
    ArchiveBudgetsV2,
    freeze_binance_archive_requests_v2,
    verify_binance_archive_request_manifest_v2,
)
from market_structure_lab.data.validation_source_v2 import (
    load_scoped_source_availability_v2,
    load_v2_boundary_publications,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--boundary-publication", type=Path, required=True)
    parser.add_argument("--source-discovery", type=Path, required=True)
    parser.add_argument("--allowed-origin", required=True)
    parser.add_argument("--redirect-policy", required=True)
    parser.add_argument("--max-redirects", type=int, required=True)
    parser.add_argument("--tls-min-version", required=True)
    parser.add_argument("--require-ca-validation", action="store_true", required=True)
    parser.add_argument(
        "--require-binance-sha256-sidecar", action="store_true", required=True
    )
    parser.add_argument("--require-local-sha256", action="store_true", required=True)
    for name in (
        "max_requests",
        "max_compressed_object_bytes",
        "max_total_compressed_bytes",
        "max_decompressed_object_bytes",
        "max_total_decompressed_bytes",
        "max_files",
        "max_disk_bytes",
        "max_rows",
        "chunk_bytes",
        "max_runtime_seconds",
        "max_concurrency",
        "retry_ceiling",
    ):
        parser.add_argument(f"--{name.replace('_', '-')}", type=int, required=True)
    parser.add_argument("--output", type=Path, required=True)
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
    availability = load_scoped_source_availability_v2(
        publication_root=args.source_discovery,
        boundary=boundary,
    )
    budgets = ArchiveBudgetsV2(
        **{
            name: getattr(args, name)
            for name in ArchiveBudgetsV2.__dataclass_fields__
        }
    )
    manifest = freeze_binance_archive_requests_v2(
        boundary=boundary,
        source_availability=availability,
        budgets=budgets,
        output=args.output,
        allowed_origin=args.allowed_origin,
        redirect_policy=args.redirect_policy,
        max_redirects=args.max_redirects,
        tls_min_version=args.tls_min_version,
        require_ca_validation=args.require_ca_validation,
        require_binance_sha256_sidecar=args.require_binance_sha256_sidecar,
        require_local_sha256=args.require_local_sha256,
    )
    verify_binance_archive_request_manifest_v2(
        manifest,
        boundary=boundary,
        source_availability=availability,
        publication_path=args.output,
    )
    print(f"{manifest.manifest_sha256} requests={len(manifest.requests)} offline=true")
    return 0


def locate_boundary_parent_publications_v2(boundary: Path) -> tuple[Path, Path]:
    parent = boundary.parent
    coverage = parent / boundary.name.replace(
        "development-boundary", "source-coverage"
    )
    split = parent / boundary.name.replace("development-boundary", "split")
    if coverage == boundary or split == boundary:
        coverage_matches = tuple(sorted(parent.glob("*source-coverage-v2.json")))
        split_matches = tuple(sorted(parent.glob("*split-v2.json")))
        if len(coverage_matches) != 1 or len(split_matches) != 1:
            raise ValueError("boundary parent publications are absent or ambiguous")
        return coverage_matches[0], split_matches[0]
    return coverage, split


def main(argv: Sequence[str] | None = None) -> int:
    return run(build_parser().parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
