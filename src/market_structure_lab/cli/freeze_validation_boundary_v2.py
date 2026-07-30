"""Freeze verified Phase 5 V2 coverage, split, and development-boundary metadata."""

from __future__ import annotations

import argparse
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import tempfile
from typing import Any

from market_structure_lab.core.artifact_io import (
    bounded_regular_files,
    path_exists_no_follow,
    read_bounded_regular,
    require_regular_directory,
    sha256_regular,
)
from market_structure_lab.core.identity import hash_json
from market_structure_lab.data.gaps import read_manifest
from market_structure_lab.data.reconciliation.manifests import (
    read_reconciliation_run,
    work_unit_manifest_from_dict,
)
from market_structure_lab.data.reconciliation.receipts import (
    read_reconciliation_promotion_receipt,
)
from market_structure_lab.research.validation_v2_models import (
    RawDumpIdentityV2,
    ReconciliationAuthorityV2,
    SourceCoverageEntryV2,
    SourceCoveragePublicationV2,
    publication_json_bytes,
)
from market_structure_lab.research.validation_v2_splits import (
    DevelopmentReadBoundaryV2,
    DevelopmentSplitPublicationV2,
    SplitPolicyV2,
    freeze_development_split_v2,
    issue_development_read_boundary_v2,
)

_MAX_JSON_BYTES = 16 * 1024 * 1024
_MAX_RR_FILES = 20_000
_PRODUCTION_DUMP_SHA256 = "1b6bcb39af41048b53729e9b094f0229163eb6ff6af9563adb666c96f5fd4da4"
_PRODUCTION_PROMOTION_SHA256 = "cb399ac01d0beeea4666da071d9186e9a144f3f7fe5e4d1bf760696c631e8529"
_PRODUCTION_COMPATIBILITY_SHA256 = (
    "482f3a09a4e24a62b0ad92f5bb90028eead67fe961eb1d3320dd38d13fd105d2"
)
_V1_PROGRAMME_ID = "VP-0d65fef04ca44dfc5ba7c7705e0be197480d7456b51178702a0011a18ab4487d"


@dataclass(frozen=True, slots=True)
class _AuthorityExpectationsV2:
    dump_sha256: str
    promotion_sha256: str
    compatibility_sha256: str
    pg_restore_list: Callable[[Path], bytes] | None = None


@dataclass(frozen=True, slots=True)
class BoundaryFreezeResultV2:
    coverage_path: Path
    split_path: Path
    boundary_path: Path
    coverage: SourceCoveragePublicationV2
    split: DevelopmentSplitPublicationV2
    boundary: DevelopmentReadBoundaryV2


def freeze_boundary_publications_v2(
    *,
    dump_path: Path,
    rr_promotion: Path,
    rr_root: Path,
    compatibility: Path,
    output_coverage: Path,
    output_split: Path,
    output_boundary: Path,
    require_zero_row_access: bool,
    require_zero_process_row_extraction: bool,
    require_zero_network_access: bool,
    _test_expectations: _AuthorityExpectationsV2 | None = None,
) -> BoundaryFreezeResultV2:
    """Recompute original metadata identities and publish three no-clobber files."""

    if not all(
        (
            require_zero_row_access,
            require_zero_process_row_extraction,
            require_zero_network_access,
        )
    ):
        raise ValueError("all zero-row/process-row-extraction/network guards are required")
    expectations = _test_expectations or _AuthorityExpectationsV2(
        dump_sha256=_PRODUCTION_DUMP_SHA256,
        promotion_sha256=_PRODUCTION_PROMOTION_SHA256,
        compatibility_sha256=_PRODUCTION_COMPATIBILITY_SHA256,
    )
    dump_identity, listing = _verify_dump(Path(dump_path), expectations)
    promotion, reconciliation, run = _verify_rr(
        Path(rr_promotion),
        Path(rr_root),
        dump_identity,
        expectations,
    )
    compatibility_bytes = _verified_bytes(
        Path(compatibility),
        expectations.compatibility_sha256,
        "compatibility",
    )
    with tempfile.TemporaryDirectory(prefix="msl-v2-compatibility-") as directory:
        captured = Path(directory) / "compatibility.json"
        captured.write_bytes(compatibility_bytes)
        compatibility_manifest = read_manifest(captured)
    if compatibility_manifest.canonical_bytes() != compatibility_bytes:
        raise ValueError("compatibility original bytes are not canonical")
    if (
        compatibility_manifest.source_identity.dump_sha256.lower() != dump_identity.dump_sha256
        or compatibility_manifest.source_identity.mapping_version != run.mapping_version
    ):
        raise ValueError("compatibility identity differs from verified dump or RR mapping")
    entries = _derive_coverage_entries(
        promotion.coverage,
        dict(compatibility_manifest.provenance_validation),
        promotion_sha256=expectations.promotion_sha256,
        compatibility_sha256=expectations.compatibility_sha256,
    )
    coverage = SourceCoveragePublicationV2.freeze(
        raw_dump=dump_identity,
        reconciliation=reconciliation,
        compatibility_metadata_sha256=hashlib.sha256(compatibility_bytes).hexdigest(),
        entries=entries,
    )
    policy = SplitPolicyV2(
        block_count=6,
        minimum_complete_days=730,
        asset_holdout_fraction_numerator=1,
        asset_holdout_fraction_denominator=5,
        asset_holdout_salt="market-structure-lab-phase5-v2-public-salt",
        purge_hours=24,
        embargo_hours=24,
        timeframes=("1m", "1h", "4h"),
    )
    split = freeze_development_split_v2(coverage=coverage, policy=policy)
    boundary = issue_development_read_boundary_v2(coverage, split)
    del listing
    return _publish_and_reopen(
        coverage=coverage,
        split=split,
        boundary=boundary,
        output_coverage=Path(output_coverage),
        output_split=Path(output_split),
        output_boundary=Path(output_boundary),
    )


def _verify_dump(
    dump_path: Path,
    expectations: _AuthorityExpectationsV2,
) -> tuple[RawDumpIdentityV2, bytes]:
    digest, byte_count = _sha256_and_size_no_follow(dump_path)
    if digest != expectations.dump_sha256:
        raise ValueError("dump SHA-256 differs from the independently frozen identity")
    listing = (
        expectations.pg_restore_list(dump_path)
        if expectations.pg_restore_list is not None
        else _run_pg_restore_list(dump_path)
    )
    if not isinstance(listing, bytes) or len(listing) > _MAX_JSON_BYTES:
        raise ValueError("pg_restore --list metadata is invalid or unbounded")
    candle_lines = tuple(
        line.strip()
        for line in listing.decode("utf-8").splitlines()
        if "TABLE DATA public candles " in line
    )
    if len(candle_lines) != 1:
        raise ValueError("pg_restore --list must contain one public.candles table-data entry")
    toc_identity = "public.candles:table-data:" + hash_json(
        "phase5-validation-candle-toc-v2", candle_lines[0]
    )
    return (
        RawDumpIdentityV2(
            dump_sha256=digest,
            byte_count=byte_count,
            pg_restore_list_sha256=hashlib.sha256(listing).hexdigest(),
            candle_table_toc_identity=toc_identity,
            source_mapping_version="callscore-candles-v1",
        ),
        listing,
    )


def _run_pg_restore_list(dump_path: Path) -> bytes:
    try:
        completed = subprocess.run(
            ["pg_restore", "--list", str(dump_path)],
            check=True,
            capture_output=True,
            timeout=120,
        )
    except (FileNotFoundError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as error:
        raise RuntimeError("pg_restore --list metadata verification failed") from error
    return completed.stdout


def _verify_rr(
    promotion_path: Path,
    rr_root: Path,
    dump_identity: RawDumpIdentityV2,
    expectations: _AuthorityExpectationsV2,
) -> tuple[Any, ReconciliationAuthorityV2, Any]:
    promotion_bytes = _verified_bytes(promotion_path, expectations.promotion_sha256, "RR promotion")
    with tempfile.TemporaryDirectory(prefix="msl-v2-promotion-") as directory:
        captured = Path(directory) / "receipt.json"
        captured.write_bytes(promotion_bytes)
        promotion = read_reconciliation_promotion_receipt(captured)
    if promotion.run_id != "RR-000008":
        raise ValueError("RR promotion must be the frozen RR-000008 authority")
    require_regular_directory(rr_root)
    relative_files = bounded_regular_files(rr_root, maximum=_MAX_RR_FILES)
    run = None
    work_manifests: dict[str, tuple[str, Path, Any]] = {}
    for relative in relative_files:
        path = rr_root / relative
        if not relative.endswith(".json"):
            continue
        raw = read_bounded_regular(path, _MAX_JSON_BYTES)
        try:
            decoded = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if not isinstance(decoded, dict):
            continue
        if (
            decoded.get("manifest_sha256") == promotion.manifest_sha256
            and "work_units" in decoded
            and "envelopes" in decoded
        ):
            candidate = read_reconciliation_run(path)
            if candidate.run_id == promotion.run_id:
                if run is not None:
                    raise ValueError("RR root contains duplicate matching run manifests")
                run = candidate
        if decoded.get("run_id") == promotion.run_id and "work_unit_id" in decoded:
            manifest = work_unit_manifest_from_dict(decoded)
            if manifest.work_unit_id in work_manifests:
                raise ValueError("RR root contains duplicate work-unit manifests")
            work_manifests[manifest.work_unit_id] = (
                hashlib.sha256(raw).hexdigest(),
                path,
                manifest,
            )
    if run is None or run.dump_sha256 != dump_identity.dump_sha256:
        raise ValueError("RR run manifest does not bind the verified dump")
    expected_units = {item.work_unit_id for item in run.work_units}
    if set(work_manifests) != expected_units:
        raise ValueError("RR work-unit manifest inventory is incomplete or unexpected")
    manifest_hashes: list[str] = []
    part_hashes: list[str] = []
    for work_unit_id in sorted(work_manifests):
        raw_sha, manifest_path, manifest = work_manifests[work_unit_id]
        manifest_hashes.append(raw_sha)
        for part in manifest.parts:
            part_path = manifest_path.parent / part.path
            if sha256_regular(part_path) != part.sha256:
                raise ValueError("RR comparison part differs from work-unit metadata")
            part_hashes.append(part.sha256)
    if not part_hashes:
        part_hashes.append(hash_json("phase5-validation-empty-comparison-parts-v2", []))
    return (
        promotion,
        ReconciliationAuthorityV2(
            promotion_receipt_sha256=hashlib.sha256(promotion_bytes).hexdigest(),
            work_unit_manifest_sha256=tuple(manifest_hashes),
            comparison_part_sha256=tuple(part_hashes),
            replacement_source_policy="rr-000008-promoted-only",
        ),
        run,
    )


def _derive_coverage_entries(
    intervals: Sequence[Any],
    states: dict[str, object],
    *,
    promotion_sha256: str,
    compatibility_sha256: str,
) -> tuple[SourceCoverageEntryV2, ...]:
    latest: dict[str, Any] = {}
    for interval in intervals:
        current = latest.get(interval.symbol)
        if current is None or (interval.end_ms, interval.start_ms) > (
            current.end_ms,
            current.start_ms,
        ):
            latest[interval.symbol] = interval
    entries = []
    for symbol in sorted(set(states) | set(latest)):
        interval = latest.get(symbol)
        state = str(states.get(symbol, "source_conflict"))
        if interval is None:
            continue
        payload = {
            "symbol": symbol,
            "start_ms": interval.start_ms,
            "end_ms": interval.end_ms,
            "state": state,
            "promotion_sha256": promotion_sha256,
            "compatibility_sha256": compatibility_sha256,
        }
        entries.append(
            SourceCoverageEntryV2(
                symbol=symbol,
                complete_start=datetime.fromtimestamp(interval.start_ms / 1000, UTC),
                complete_end=datetime.fromtimestamp(interval.end_ms / 1000, UTC),
                timeframes=("1m", "1h", "4h"),
                source_conflict=state != "compatible",
                mapping_compatible=state == "compatible",
                metadata_sha256=hash_json("phase5-validation-source-coverage-entry-v2", payload),
            )
        )
    return tuple(entries)


def _publish_and_reopen(
    *,
    coverage: SourceCoveragePublicationV2,
    split: DevelopmentSplitPublicationV2,
    boundary: DevelopmentReadBoundaryV2,
    output_coverage: Path,
    output_split: Path,
    output_boundary: Path,
) -> BoundaryFreezeResultV2:
    outputs = (output_coverage, output_split, output_boundary)
    if len(set(outputs)) != 3:
        raise ValueError("coverage, split, and boundary outputs must be distinct")
    for output in outputs:
        _reject_v1_root(output)
        if path_exists_no_follow(output):
            raise FileExistsError(f"refusing stale or concurrent output: {output}")
        require_regular_directory(output.parent)
    contents = (
        coverage.canonical_bytes,
        split.canonical_bytes,
        boundary.canonical_bytes,
    )
    temporaries: list[Path] = []
    created: list[Path] = []
    try:
        for output, content in zip(outputs, contents, strict=True):
            descriptor, temporary_name = tempfile.mkstemp(
                prefix=f".{output.name}.", suffix=".tmp", dir=output.parent
            )
            temporary = Path(temporary_name)
            temporaries.append(temporary)
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
        for temporary, output in zip(temporaries, outputs, strict=True):
            os.link(temporary, output, follow_symlinks=False)
            created.append(output)
        for parent in {output.parent for output in outputs}:
            descriptor = os.open(parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
        reopened_coverage = SourceCoveragePublicationV2.from_dict(
            _read_publication(output_coverage)
        )
        reopened_split = DevelopmentSplitPublicationV2.from_dict(
            _read_publication(output_split), reopened_coverage
        )
        reopened_boundary = DevelopmentReadBoundaryV2.from_publication_dict(
            _read_publication(output_boundary), reopened_coverage, reopened_split
        )
        if tuple(read_bounded_regular(path, _MAX_JSON_BYTES) for path in outputs) != contents:
            raise ValueError("published bytes differ from verified staged publications")
    except BaseException:
        for path in created:
            path.unlink(missing_ok=True)
        raise
    finally:
        for path in temporaries:
            path.unlink(missing_ok=True)
    return BoundaryFreezeResultV2(
        coverage_path=output_coverage,
        split_path=output_split,
        boundary_path=output_boundary,
        coverage=reopened_coverage,
        split=reopened_split,
        boundary=reopened_boundary,
    )


def _read_publication(path: Path) -> dict[str, Any]:
    content = read_bounded_regular(path, _MAX_JSON_BYTES)
    try:
        decoded = json.loads(content)
    except json.JSONDecodeError as error:
        raise ValueError("published V2 metadata is not JSON") from error
    if not isinstance(decoded, dict):
        raise ValueError("published V2 metadata must be an object")
    if publication_json_bytes(decoded) != content:
        raise ValueError("published V2 metadata is not canonical")
    return decoded


def _verified_bytes(path: Path, expected_sha256: str, label: str) -> bytes:
    content = read_bounded_regular(path, _MAX_JSON_BYTES)
    if hashlib.sha256(content).hexdigest() != expected_sha256:
        raise ValueError(f"{label} SHA-256 differs from independently frozen bytes")
    return content


def _sha256_and_size_no_follow(path: Path) -> tuple[str, int]:
    absolute = path if path.is_absolute() else Path.cwd() / path
    if ".." in absolute.parts:
        raise RuntimeError("dump path cannot contain traversal")
    for parent in reversed(absolute.parents):
        if stat.S_ISLNK(parent.lstat().st_mode):
            raise RuntimeError("dump path contains a symlink ancestor")
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(absolute, flags)
    digest = hashlib.sha256()
    size = 0
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise RuntimeError("dump path must be a regular file")
        while chunk := os.read(descriptor, 1024 * 1024):
            digest.update(chunk)
            size += len(chunk)
        if size != metadata.st_size:
            raise RuntimeError("dump size changed during metadata verification")
    finally:
        os.close(descriptor)
    return digest.hexdigest(), size


def _reject_v1_root(path: Path) -> None:
    if any(parent.name == _V1_PROGRAMME_ID for parent in (path, *path.parents)):
        raise ValueError("V2 publication must not write under the existing V1 programme root")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Freeze Phase 5 V2 development-only metadata publications"
    )
    parser.add_argument("--dump-path", required=True, type=Path)
    parser.add_argument("--rr-promotion", required=True, type=Path)
    parser.add_argument("--rr-root", required=True, type=Path)
    parser.add_argument("--compatibility", required=True, type=Path)
    parser.add_argument("--output-coverage", required=True, type=Path)
    parser.add_argument("--output-split", required=True, type=Path)
    parser.add_argument("--output-boundary", required=True, type=Path)
    parser.add_argument("--require-zero-row-access", action="store_true")
    parser.add_argument("--require-zero-process-row-extraction", action="store_true")
    parser.add_argument("--require-zero-network-access", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    result = freeze_boundary_publications_v2(
        dump_path=args.dump_path,
        rr_promotion=args.rr_promotion,
        rr_root=args.rr_root,
        compatibility=args.compatibility,
        output_coverage=args.output_coverage,
        output_split=args.output_split,
        output_boundary=args.output_boundary,
        require_zero_row_access=args.require_zero_row_access,
        require_zero_process_row_extraction=args.require_zero_process_row_extraction,
        require_zero_network_access=args.require_zero_network_access,
    )
    print(
        json.dumps(
            {
                "coverage_identity": result.coverage.coverage_identity.value,
                "split_identity": result.split.split_identity.value,
                "boundary_sha256": result.boundary.boundary_sha256,
                "entrypoint_registration": "pending_integration_commit_C",
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "BoundaryFreezeResultV2",
    "build_parser",
    "freeze_boundary_publications_v2",
    "main",
]
