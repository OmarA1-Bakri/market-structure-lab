"""Atomic bounded Parquet publication for per-key reconciliation evidence."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from collections import Counter
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime
from pathlib import Path

import polars as pl

from market_structure_lab.data.reconciliation.manifests import (
    LedgerPart,
    MAX_LEDGER_ROWS_PER_PART,
    ReconciliationRunManifest,
    SourceArtifactIdentity,
    WorkUnitManifest,
    work_unit_manifest_from_dict,
)
from market_structure_lab.data.reconciliation.models import (
    MINUTE_MS,
    ReconciliationClass,
    ReconciliationRecord,
    ReconciliationWorkUnit,
)

_LEDGER_SCHEMA = {
    "run_id": pl.String,
    "work_unit_id": pl.String,
    "symbol": pl.String,
    "timeframe": pl.String,
    "open_time_ms": pl.Int64,
    "classification": pl.String,
    "dump_row_sha256": pl.String,
    "binance_row_sha256": pl.String,
    "differing_fields_json": pl.String,
}


def publish_work_unit(
    records: Iterable[ReconciliationRecord],
    *,
    output_root: Path,
    run: ReconciliationRunManifest,
    work_unit: ReconciliationWorkUnit,
    source_artifacts: Sequence[SourceArtifactIdentity],
    max_rows_per_part: int = 100_000,
) -> WorkUnitManifest:
    """Write and verify one immutable work-unit ledger without unbounded buffering."""
    if max_rows_per_part < 1:
        raise ValueError("max_rows_per_part must be positive")
    if max_rows_per_part > MAX_LEDGER_ROWS_PER_PART:
        raise ValueError("max_rows_per_part exceeds the Phase 0 safe maximum")
    if work_unit not in run.work_units:
        raise ValueError("work unit is not frozen in the reconciliation run")
    publication_path = _publication_path(run.run_id, work_unit)
    final = output_root / publication_path
    stage = final.parent / f".{work_unit.work_unit_id}.stage"
    if stage.exists():
        shutil.rmtree(stage)
    stage.mkdir(parents=True)

    try:
        manifest = _write_stage(
            records,
            stage=stage,
            publication_path=publication_path,
            run=run,
            work_unit=work_unit,
            source_artifacts=source_artifacts,
            max_rows_per_part=max_rows_per_part,
        )
        if final.exists():
            existing = verify_work_unit_publication(final)
            if existing != manifest:
                raise ValueError("existing work-unit publication contains different content")
            return existing
        final.parent.mkdir(parents=True, exist_ok=True)
        os.replace(stage, final)
        return verify_work_unit_publication(final)
    finally:
        if stage.exists():
            shutil.rmtree(stage)


def read_work_unit_manifest(path: Path) -> WorkUnitManifest:
    """Read one manifest and reject any checksum/content disagreement."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError("work-unit manifest is not valid JSON") from error
    if not isinstance(raw, dict):
        raise ValueError("work-unit manifest must be a JSON object")
    supplied = raw.get("manifest_sha256")
    logical = {key: value for key, value in raw.items() if key != "manifest_sha256"}
    actual = hashlib.sha256(
        json.dumps(logical, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
    ).hexdigest()
    if supplied != actual:
        raise ValueError("work-unit manifest checksum does not match its content")
    return work_unit_manifest_from_dict(raw)


def _write_stage(
    records: Iterable[ReconciliationRecord],
    *,
    stage: Path,
    publication_path: str,
    run: ReconciliationRunManifest,
    work_unit: ReconciliationWorkUnit,
    source_artifacts: Sequence[SourceArtifactIdentity],
    max_rows_per_part: int,
) -> WorkUnitManifest:
    classifications: Counter[str] = Counter()
    differing_fields: Counter[str] = Counter()
    replacement_digest = hashlib.sha256()
    replacement_count = 0
    parts: list[LedgerPart] = []
    buffer: list[dict[str, object]] = []
    max_buffered = 0
    expected_timestamp = work_unit.start_ms

    def flush() -> None:
        nonlocal max_buffered
        if not buffer:
            return
        max_buffered = max(max_buffered, len(buffer))
        path = stage / f"part-{len(parts):05d}.parquet"
        pl.DataFrame(buffer, schema=_LEDGER_SCHEMA).write_parquet(path)
        parts.append(
            LedgerPart(
                path=path.name,
                sha256=sha256_file(path),
                row_count=len(buffer),
            )
        )
        buffer.clear()

    for record in records:
        if (
            record.symbol != work_unit.symbol
            or record.timeframe != work_unit.timeframe
            or record.open_time_ms != expected_timestamp
        ):
            raise ValueError("reconciliation records must be ordered and cover the work unit")
        expected_timestamp += MINUTE_MS
        classifications[record.classification.value] += 1
        differing_fields.update(record.differing_fields)
        if record.classification in (
            ReconciliationClass.BINANCE_CORRECTION,
            ReconciliationClass.BINANCE_FILL,
        ):
            if record.binance_row_sha256 is None:
                raise ValueError("replacement evidence requires a Binance row hash")
            replacement_count += 1
            replacement_digest.update(
                json.dumps(
                    {
                        "binance_row_sha256": record.binance_row_sha256,
                        "open_time_ms": record.open_time_ms,
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode()
            )
        buffer.append(
            {
                "run_id": run.run_id,
                "work_unit_id": work_unit.work_unit_id,
                "symbol": record.symbol,
                "timeframe": record.timeframe,
                "open_time_ms": record.open_time_ms,
                "classification": record.classification.value,
                "dump_row_sha256": record.dump_row_sha256,
                "binance_row_sha256": record.binance_row_sha256,
                "differing_fields_json": json.dumps(
                    list(record.differing_fields),
                    separators=(",", ":"),
                ),
            }
        )
        if len(buffer) == max_rows_per_part:
            flush()
    flush()
    if expected_timestamp != work_unit.end_ms:
        raise ValueError("reconciliation records do not cover every work-unit minute")

    ordered_artifacts = tuple(
        sorted(
            set(source_artifacts),
            key=lambda item: (
                item.location,
                item.payload_sha256,
                item.source_revision,
            ),
        )
    )
    status = (
        "source_unavailable"
        if classifications[ReconciliationClass.SOURCE_UNAVAILABLE.value]
        else "completed"
    )
    values = {
        "run_id": run.run_id,
        "work_unit_id": work_unit.work_unit_id,
        "publication_path": publication_path,
        "row_count": sum(classifications.values()),
        "classification_counts": tuple(sorted(classifications.items())),
        "differing_field_counts": tuple(sorted(differing_fields.items())),
        "source_artifacts": ordered_artifacts,
        "replacement_row_count": replacement_count,
        "replacement_logical_sha256": replacement_digest.hexdigest(),
        "max_rows_per_part": max_rows_per_part,
        "max_buffered_rows": max_buffered,
        "status": status,
        "parts": tuple(parts),
    }
    provisional = WorkUnitManifest.__new__(WorkUnitManifest)
    for name, value in values.items():
        object.__setattr__(provisional, name, value)
    object.__setattr__(provisional, "manifest_sha256", "0" * 64)
    digest = provisional.sha256()
    manifest = WorkUnitManifest(**values, manifest_sha256=digest)  # type: ignore[arg-type]
    (stage / "manifest.json").write_text(manifest.to_json(), encoding="utf-8")
    (stage / "_SUCCESS").write_text(manifest.manifest_sha256 + "\n", encoding="utf-8")
    return manifest


def verify_work_unit_publication(directory: Path) -> WorkUnitManifest:
    """Verify one complete publication, including marker, parts, and row counts."""
    manifest = read_work_unit_manifest(directory / "manifest.json")
    try:
        success = (directory / "_SUCCESS").read_text(encoding="utf-8").strip()
    except OSError as error:
        raise ValueError("work-unit publication has no success marker") from error
    if success != manifest.manifest_sha256:
        raise ValueError("work-unit success marker checksum does not match the manifest")
    expected = {item.path for item in manifest.parts}
    actual = {path.name for path in directory.glob("part-*.parquet")}
    if actual != expected:
        raise ValueError("work-unit publication contains missing or unmanifested Parquet parts")
    for part in manifest.parts:
        path = directory / part.path
        if sha256_file(path) != part.sha256:
            raise ValueError("work-unit Parquet part checksum does not match the manifest")
        row_count = int(
            pl.scan_parquet(path)
            .select(pl.len().alias("row_count"))
            .collect(engine="streaming")
            .item()
        )
        if row_count != part.row_count:
            raise ValueError("work-unit Parquet part row count does not match the manifest")
    return manifest


def verify_reconciliation_run_publication(
    output_root: Path,
    run: ReconciliationRunManifest,
    *,
    expected_manifest_sha256: str,
) -> tuple[WorkUnitManifest, ...]:
    """Verify the exact terminal publication set for one frozen run."""
    if run.manifest_sha256 != expected_manifest_sha256:
        raise ValueError("reconciliation run manifest does not match the expected SHA-256")
    run_root = output_root / f"run_id={run.run_id}"
    stages = tuple(sorted(path for path in run_root.glob("**/.*.stage") if path.is_dir()))
    if stages:
        raise ValueError("reconciliation run contains stale staging directories")

    candidates = tuple(sorted(run_root.glob("**/manifest.json")))
    parsed: dict[str, tuple[Path, WorkUnitManifest]] = {}
    for path in candidates:
        manifest = read_work_unit_manifest(path)
        if manifest.work_unit_id in parsed:
            raise ValueError("reconciliation run contains a duplicate work-unit publication")
        parsed[manifest.work_unit_id] = (path.parent, manifest)

    expected_by_id = {item.work_unit_id: item for item in run.work_units}
    expected = set(expected_by_id)
    found = set(parsed)
    if found != expected:
        missing = sorted(expected - found)
        extra = sorted(found - expected)
        raise ValueError(f"work-unit evidence is incomplete; missing={missing}, extra={extra}")

    verified: dict[str, WorkUnitManifest] = {}
    for work_unit_id, (directory, parsed_manifest) in parsed.items():
        manifest = verify_work_unit_publication(directory)
        if manifest != parsed_manifest:
            raise ValueError("work-unit publication changed during verification")
        unit = expected_by_id[work_unit_id]
        publication_path = _publication_path(run.run_id, unit)
        if (
            manifest.run_id != run.run_id
            or manifest.publication_path != publication_path
            or directory != output_root / publication_path
        ):
            raise ValueError("work-unit publication path does not match the frozen run")
        if manifest.status == "failed":
            raise ValueError("failed reconciliation work units are not terminal evidence")
        expected_rows = (unit.end_ms - unit.start_ms) // MINUTE_MS
        if manifest.row_count != expected_rows:
            raise ValueError("work-unit row count does not match the frozen minute count")
        _verify_ledger_against_work_unit(directory, manifest, unit)
        verified[work_unit_id] = manifest
    return tuple(verified[item.work_unit_id] for item in run.work_units)


def _verify_ledger_against_work_unit(
    directory: Path,
    manifest: WorkUnitManifest,
    work_unit: ReconciliationWorkUnit,
) -> None:
    expected_timestamp = work_unit.start_ms
    classifications: Counter[str] = Counter()
    differing_fields: Counter[str] = Counter()
    replacement_count = 0
    replacement_digest = hashlib.sha256()
    for part in manifest.parts:
        frame = pl.read_parquet(directory / part.path)
        if frame.schema != _LEDGER_SCHEMA:
            raise ValueError("work-unit ledger schema does not match the canonical schema")
        for raw in frame.iter_rows(named=True):
            try:
                classification = ReconciliationClass(str(raw["classification"]))
                decoded_fields = json.loads(str(raw["differing_fields_json"]))
            except (ValueError, json.JSONDecodeError) as error:
                raise ValueError(
                    "work-unit ledger contains invalid classification evidence"
                ) from error
            if not isinstance(decoded_fields, list) or not all(
                isinstance(item, str) for item in decoded_fields
            ):
                raise ValueError("work-unit ledger contains invalid differing-field evidence")
            record = ReconciliationRecord(
                symbol=str(raw["symbol"]),
                timeframe=str(raw["timeframe"]),
                open_time_ms=int(raw["open_time_ms"]),
                classification=classification,
                dump_row_sha256=(
                    None if raw["dump_row_sha256"] is None else str(raw["dump_row_sha256"])
                ),
                binance_row_sha256=(
                    None if raw["binance_row_sha256"] is None else str(raw["binance_row_sha256"])
                ),
                differing_fields=tuple(decoded_fields),
            )
            _validate_record_semantics(record)
            if (
                str(raw["run_id"]) != manifest.run_id
                or str(raw["work_unit_id"]) != work_unit.work_unit_id
                or record.symbol != work_unit.symbol
                or record.timeframe != work_unit.timeframe
                or record.open_time_ms != expected_timestamp
            ):
                raise ValueError(
                    "work-unit ledger does not match the frozen identity and minute order"
                )
            expected_timestamp += MINUTE_MS
            classifications[record.classification.value] += 1
            differing_fields.update(record.differing_fields)
            if record.classification in (
                ReconciliationClass.BINANCE_CORRECTION,
                ReconciliationClass.BINANCE_FILL,
            ):
                if record.binance_row_sha256 is None:
                    raise ValueError("replacement evidence requires a Binance row hash")
                replacement_count += 1
                replacement_digest.update(
                    json.dumps(
                        {
                            "binance_row_sha256": record.binance_row_sha256,
                            "open_time_ms": record.open_time_ms,
                        },
                        sort_keys=True,
                        separators=(",", ":"),
                    ).encode()
                )
    if expected_timestamp != work_unit.end_ms:
        raise ValueError("work-unit ledger does not cover every frozen minute")
    if tuple(sorted(classifications.items())) != manifest.classification_counts:
        raise ValueError("work-unit ledger classifications do not match the manifest")
    expected_status = (
        "source_unavailable"
        if classifications[ReconciliationClass.SOURCE_UNAVAILABLE.value]
        else "completed"
    )
    if manifest.status != expected_status:
        raise ValueError("work-unit terminal status does not match the verified ledger")
    if tuple(sorted(differing_fields.items())) != manifest.differing_field_counts:
        raise ValueError("work-unit ledger differing fields do not match the manifest")
    if replacement_count != manifest.replacement_row_count:
        raise ValueError("work-unit ledger replacements do not match the manifest count")
    if replacement_digest.hexdigest() != manifest.replacement_logical_sha256:
        raise ValueError("work-unit ledger replacements do not match the manifest hash")


def _validate_record_semantics(record: ReconciliationRecord) -> None:
    dump_hash = record.dump_row_sha256
    binance_hash = record.binance_row_sha256
    differing = record.differing_fields
    if record.classification is ReconciliationClass.EXACT_MATCH:
        valid = dump_hash is not None and binance_hash is not None and not differing
    elif record.classification is ReconciliationClass.BINANCE_CORRECTION:
        valid = (
            dump_hash is not None
            and binance_hash is not None
            and dump_hash != binance_hash
            and bool(differing)
        )
    elif record.classification is ReconciliationClass.BINANCE_FILL:
        valid = dump_hash is None and binance_hash is not None and not differing
    else:
        valid = binance_hash is None and not differing
    if not valid:
        raise ValueError("work-unit ledger classification does not match its row-hash evidence")


def _publication_path(run_id: str, work_unit: ReconciliationWorkUnit) -> str:
    start = datetime.fromtimestamp(work_unit.start_ms / 1_000, tz=UTC)
    return (
        f"run_id={run_id}/symbol={work_unit.symbol}/year={start.year:04d}/month={start.month:02d}"
    )


def sha256_file(path: Path) -> str:
    """Return a bounded-memory SHA-256 digest for one publication file."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


__all__ = [
    "publish_work_unit",
    "read_work_unit_manifest",
    "sha256_file",
    "verify_reconciliation_run_publication",
    "verify_work_unit_publication",
]
