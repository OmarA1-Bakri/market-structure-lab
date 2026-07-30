"""Freeze metadata-only Phase 5 V2 coverage, split, and read-boundary publications."""

from __future__ import annotations

import argparse
from collections.abc import Callable, Sequence
from dataclasses import dataclass
import json
import os
from pathlib import Path
import shutil
import tempfile
from typing import Any

from market_structure_lab.core.identity import hash_json
from market_structure_lab.research.validation_v2_models import (
    RawDumpIdentityV2,
    ReconciliationAuthorityV2,
    SourceCoverageEntryV2,
    SourceCoveragePublicationV2,
)
from market_structure_lab.research.validation_v2_splits import (
    DevelopmentReadBoundaryV2,
    DevelopmentSplitPublicationV2,
    SplitPolicyV2,
    freeze_development_split_v2,
    issue_development_read_boundary_v2,
)

_MAX_METADATA_BYTES = 16 * 1024 * 1024
_INPUT_SCHEMA = "phase5-validation-boundary-freeze-input-v2"
_INPUT_DOMAIN = "phase5-validation-boundary-freeze-input-v2"


@dataclass(frozen=True, slots=True)
class BoundaryFreezeAdaptersV2:
    """Forbidden I/O adapters exposed only so tests can prove they stay untouched."""

    row_reader: Callable[..., object] | None = None
    process_runner: Callable[..., object] | None = None
    network_reader: Callable[..., object] | None = None


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
    metadata_path: Path,
    output_dir: Path,
    adapters: BoundaryFreezeAdaptersV2 | None = None,
) -> BoundaryFreezeResultV2:
    """Verify metadata and atomically publish the V2 boundary DAG without I/O adapters."""

    if adapters is not None and not isinstance(adapters, BoundaryFreezeAdaptersV2):
        raise TypeError("adapters must be BoundaryFreezeAdaptersV2")
    payload = _read_metadata(metadata_path)
    coverage, policy = _verify_freeze_metadata(payload)
    split = freeze_development_split_v2(coverage=coverage, policy=policy)
    boundary = issue_development_read_boundary_v2(coverage, split)
    output = Path(output_dir)
    if os.path.lexists(output):
        raise FileExistsError(f"refusing to overwrite existing publication root: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.parent.is_symlink():
        raise RuntimeError("publication parent must not be a symlink")
    staging = Path(
        tempfile.mkdtemp(prefix=f".{output.name}.tmp-", dir=str(output.parent))
    )
    try:
        _write_no_clobber(staging / "source-coverage-v2.json", coverage.to_dict())
        _write_no_clobber(staging / "development-split-v2.json", split.to_dict())
        _write_no_clobber(
            staging / "development-read-boundary-v2.json", boundary.to_dict()
        )
        os.replace(staging, output)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return BoundaryFreezeResultV2(
        coverage_path=output / "source-coverage-v2.json",
        split_path=output / "development-split-v2.json",
        boundary_path=output / "development-read-boundary-v2.json",
        coverage=coverage,
        split=split,
        boundary=boundary,
    )


def _read_metadata(path: Path) -> dict[str, Any]:
    source = Path(path)
    if source.is_symlink() or not source.is_file():
        raise ValueError("metadata path must be a regular file")
    if source.stat().st_size > _MAX_METADATA_BYTES:
        raise ValueError("metadata file exceeds the bounded size limit")
    try:
        payload = json.loads(source.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("metadata file is not valid UTF-8 JSON") from error
    if not isinstance(payload, dict):
        raise TypeError("metadata root must be an object")
    return payload


def _verify_freeze_metadata(
    payload: dict[str, Any],
) -> tuple[SourceCoveragePublicationV2, SplitPolicyV2]:
    expected = {
        "schema_version",
        "scope",
        "component",
        "final_holdout_access_count",
        "raw_dump",
        "reconciliation",
        "compatibility",
        "split_policy",
        "metadata_sha256",
    }
    if set(payload) != expected:
        raise ValueError("metadata has unexpected or missing fields")
    if payload["schema_version"] != _INPUT_SCHEMA:
        raise ValueError("metadata schema_version is not the V2 freezer schema")
    if payload["scope"] != "development_metadata_only":
        raise ValueError("metadata scope must be development_metadata_only")
    if payload["component"] != "development":
        raise ValueError("metadata component must be development")
    if payload["final_holdout_access_count"] != 0:
        raise ValueError("metadata final_holdout_access_count must be zero")
    preimage = {key: value for key, value in payload.items() if key != "metadata_sha256"}
    if payload["metadata_sha256"] != hash_json(_INPUT_DOMAIN, preimage):
        raise ValueError("metadata_sha256 does not match canonical metadata")
    compatibility = _exact_mapping(
        payload["compatibility"], {"metadata_sha256", "entries"}, "compatibility"
    )
    raw_entries = compatibility["entries"]
    if not isinstance(raw_entries, list) or not raw_entries:
        raise TypeError("compatibility entries must be a non-empty list")
    coverage = SourceCoveragePublicationV2.freeze(
        raw_dump=RawDumpIdentityV2.from_dict(payload["raw_dump"]),
        reconciliation=ReconciliationAuthorityV2.from_dict(payload["reconciliation"]),
        compatibility_metadata_sha256=compatibility["metadata_sha256"],  # type: ignore[arg-type]
        entries=tuple(SourceCoverageEntryV2.from_dict(item) for item in raw_entries),
    )
    return coverage, SplitPolicyV2.from_dict(payload["split_policy"])


def _write_no_clobber(path: Path, payload: object) -> None:
    encoded = (
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
    except BaseException:
        try:
            os.close(descriptor)
        except OSError:
            pass
        raise


def _exact_mapping(
    payload: object, expected: set[str], label: str
) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise TypeError(f"{label} must be an object")
    if set(payload) != expected:
        raise ValueError(f"{label} has unexpected or missing fields")
    return payload


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Freeze Phase 5 V2 development boundary metadata"
    )
    parser.add_argument("--metadata", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    result = freeze_boundary_publications_v2(
        metadata_path=args.metadata,
        output_dir=args.output_dir,
    )
    print(
        json.dumps(
            {
                "coverage_identity": result.coverage.coverage_identity.value,
                "split_identity": result.split.split_identity.value,
                "boundary_sha256": result.boundary.boundary_sha256,
                "output_dir": str(Path(args.output_dir)),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "BoundaryFreezeAdaptersV2",
    "BoundaryFreezeResultV2",
    "freeze_boundary_publications_v2",
    "main",
]
