"""Canonical identities for immutable candle reconciliation runs and work units."""

from __future__ import annotations

import hashlib
import json
import re
import os
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any, Literal
from pathlib import Path

from market_structure_lab.data.reconciliation.models import ReconciliationWorkUnit

_RUN_ID = re.compile(r"^RR-[0-9]{6}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_COMMIT = re.compile(r"^[0-9a-f]{7,64}$")
_COMPONENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
WorkUnitStatus = Literal["completed", "source_unavailable", "failed"]


@dataclass(frozen=True, slots=True)
class TradingEnvelope:
    symbol: str
    timeframe: str
    start_ms: int
    end_ms: int

    def __post_init__(self) -> None:
        ReconciliationWorkUnit.create(
            self.symbol,
            self.timeframe,
            self.start_ms,
            self.end_ms,
        )


@dataclass(frozen=True, slots=True)
class SourceArtifactIdentity:
    location: str
    payload_sha256: str
    published_sha256: str | None
    source_revision: str
    retrieved_at: str
    excluded_row_count: int = 0
    integrity_notes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.location:
            raise ValueError("source artifact location must not be empty")
        _require_sha(self.payload_sha256, "payload_sha256")
        if self.published_sha256 is not None:
            _require_sha(self.published_sha256, "published_sha256")
        _require_component(self.source_revision, "source_revision")
        _parse_utc(self.retrieved_at)
        if self.excluded_row_count < 0:
            raise ValueError("excluded source row count cannot be negative")
        if self.integrity_notes != tuple(sorted(set(self.integrity_notes))):
            raise ValueError("source integrity notes must be unique and sorted")
        if any(not note for note in self.integrity_notes):
            raise ValueError("source integrity notes cannot be empty")


@dataclass(frozen=True, slots=True)
class ReconciliationRunManifest:
    run_id: str
    cutoff: str
    dump_sha256: str
    source_row_count: int
    mapping_version: str
    candidate_venue: str
    market_type: str
    source_revision: str
    algorithm_version: str
    code_commit: str
    uv_lock_sha256: str
    envelopes: tuple[TradingEnvelope, ...]
    work_units: tuple[ReconciliationWorkUnit, ...]
    manifest_sha256: str

    def __post_init__(self) -> None:
        if _RUN_ID.fullmatch(self.run_id) is None:
            raise ValueError("run_id must match RR-######")
        _parse_utc(self.cutoff)
        _require_sha(self.dump_sha256, "dump_sha256")
        if self.source_row_count < 0:
            raise ValueError("source_row_count cannot be negative")
        for name in (
            "mapping_version",
            "candidate_venue",
            "market_type",
            "source_revision",
            "algorithm_version",
        ):
            _require_component(str(getattr(self, name)), name)
        if _COMMIT.fullmatch(self.code_commit) is None:
            raise ValueError("code_commit must be a hexadecimal Git object ID")
        _require_sha(self.uv_lock_sha256, "uv_lock_sha256")
        if not self.envelopes or not self.work_units:
            raise ValueError("reconciliation run requires envelopes and work units")
        if self.envelopes != tuple(
            sorted(self.envelopes, key=lambda item: (item.symbol, item.timeframe, item.start_ms))
        ):
            raise ValueError("envelopes must use canonical sorted order")
        envelope_keys = tuple(
            (item.symbol, item.timeframe, item.start_ms, item.end_ms) for item in self.envelopes
        )
        if len(envelope_keys) != len(set(envelope_keys)):
            raise ValueError("reconciliation run contains duplicate envelopes")
        if self.work_units != tuple(
            sorted(self.work_units, key=lambda item: (item.symbol, item.timeframe, item.start_ms))
        ):
            raise ValueError("work units must use canonical sorted order")
        unit_ids = tuple(item.work_unit_id for item in self.work_units)
        if len(unit_ids) != len(set(unit_ids)):
            raise ValueError("reconciliation run contains duplicate work units")
        for unit in self.work_units:
            if not any(
                unit.symbol == envelope.symbol
                and unit.timeframe == envelope.timeframe
                and envelope.start_ms <= unit.start_ms < unit.end_ms <= envelope.end_ms
                for envelope in self.envelopes
            ):
                raise ValueError("every work unit must lie inside one frozen trading envelope")
        _require_sha(self.manifest_sha256, "manifest_sha256")
        if self.sha256() != self.manifest_sha256:
            raise ValueError("reconciliation run manifest checksum does not match its content")

    def logical_dict(self) -> dict[str, object]:
        return {
            "run_id": self.run_id,
            "cutoff": self.cutoff,
            "dump_sha256": self.dump_sha256,
            "source_row_count": self.source_row_count,
            "mapping_version": self.mapping_version,
            "candidate_venue": self.candidate_venue,
            "market_type": self.market_type,
            "source_revision": self.source_revision,
            "algorithm_version": self.algorithm_version,
            "code_commit": self.code_commit,
            "uv_lock_sha256": self.uv_lock_sha256,
            "envelopes": [asdict(item) for item in self.envelopes],
            "work_units": [asdict(item) for item in self.work_units],
        }

    def sha256(self) -> str:
        return hashlib.sha256(_canonical_json(self.logical_dict())).hexdigest()

    def to_json(self) -> str:
        payload = self.logical_dict()
        payload["manifest_sha256"] = self.manifest_sha256
        return _pretty_json(payload)


MAX_LEDGER_ROWS_PER_PART = 100_000


@dataclass(frozen=True, slots=True)
class LedgerPart:
    path: str
    sha256: str
    row_count: int

    def __post_init__(self) -> None:
        if not re.fullmatch(r"part-[0-9]{5}\.parquet", self.path):
            raise ValueError("ledger part path must be a numbered Parquet part")
        _require_sha(self.sha256, "ledger part sha256")
        if self.row_count < 1:
            raise ValueError("ledger part row_count must be positive")
        if self.row_count > MAX_LEDGER_ROWS_PER_PART:
            raise ValueError("ledger part row_count exceeds the Phase 0 safe maximum")


@dataclass(frozen=True, slots=True)
class WorkUnitManifest:
    run_id: str
    work_unit_id: str
    publication_path: str
    row_count: int
    classification_counts: tuple[tuple[str, int], ...]
    differing_field_counts: tuple[tuple[str, int], ...]
    source_artifacts: tuple[SourceArtifactIdentity, ...]
    replacement_row_count: int
    replacement_logical_sha256: str
    max_rows_per_part: int
    max_buffered_rows: int
    status: WorkUnitStatus
    parts: tuple[LedgerPart, ...]
    manifest_sha256: str

    def __post_init__(self) -> None:
        if _RUN_ID.fullmatch(self.run_id) is None:
            raise ValueError("run_id must match RR-######")
        if not re.fullmatch(r"[0-9a-f]{24}", self.work_unit_id):
            raise ValueError("work_unit_id must be a 24-character lowercase hex identity")
        if not self.publication_path or "\\" in self.publication_path:
            raise ValueError("publication_path must be a portable relative path")
        if self.row_count < 0 or self.replacement_row_count < 0:
            raise ValueError("work-unit row counts cannot be negative")
        if self.max_rows_per_part < 1:
            raise ValueError("max_rows_per_part must be positive")
        if self.max_rows_per_part > MAX_LEDGER_ROWS_PER_PART:
            raise ValueError("max_rows_per_part exceeds the Phase 0 safe maximum")
        if not 0 <= self.max_buffered_rows <= self.max_rows_per_part:
            raise ValueError("max_buffered_rows exceeds the configured bound")
        if self.status not in ("completed", "source_unavailable", "failed"):
            raise ValueError("unsupported work-unit status")
        _require_sorted_counts(self.classification_counts, "classification_counts")
        _require_sorted_counts(self.differing_field_counts, "differing_field_counts")
        if sum(value for _, value in self.classification_counts) != self.row_count:
            raise ValueError("classification counts do not match work-unit row count")
        if sum(item.row_count for item in self.parts) != self.row_count:
            raise ValueError("ledger part counts do not match work-unit row count")
        if any(item.row_count > self.max_rows_per_part for item in self.parts):
            raise ValueError("ledger part row_count exceeds the manifest part-size bound")
        paths = tuple(item.path for item in self.parts)
        if paths != tuple(sorted(paths)) or len(paths) != len(set(paths)):
            raise ValueError("ledger parts must have unique canonical order")
        _require_sha(self.replacement_logical_sha256, "replacement_logical_sha256")
        _require_sha(self.manifest_sha256, "manifest_sha256")
        if self.sha256() != self.manifest_sha256:
            raise ValueError("work-unit manifest checksum does not match its content")

    def logical_dict(self) -> dict[str, object]:
        return {
            "run_id": self.run_id,
            "work_unit_id": self.work_unit_id,
            "publication_path": self.publication_path,
            "row_count": self.row_count,
            "classification_counts": dict(self.classification_counts),
            "differing_field_counts": dict(self.differing_field_counts),
            "source_artifacts": [_source_artifact_dict(item) for item in self.source_artifacts],
            "replacement_row_count": self.replacement_row_count,
            "replacement_logical_sha256": self.replacement_logical_sha256,
            "max_rows_per_part": self.max_rows_per_part,
            "max_buffered_rows": self.max_buffered_rows,
            "status": self.status,
            "parts": [asdict(item) for item in self.parts],
        }

    def sha256(self) -> str:
        return hashlib.sha256(_canonical_json(self.logical_dict())).hexdigest()

    def to_json(self) -> str:
        payload = self.logical_dict()
        payload["manifest_sha256"] = self.manifest_sha256
        return _pretty_json(payload)


def freeze_reconciliation_run(
    *,
    run_id: str,
    cutoff: datetime,
    dump_sha256: str,
    source_row_count: int,
    mapping_version: str,
    candidate_venue: str,
    market_type: str,
    source_revision: str,
    algorithm_version: str,
    code_commit: str,
    uv_lock_sha256: str,
    envelopes: Sequence[TradingEnvelope],
    work_units: Sequence[ReconciliationWorkUnit],
) -> ReconciliationRunManifest:
    """Freeze all source and work identities before source retrieval starts."""
    if cutoff.tzinfo is None or cutoff.utcoffset() is None:
        raise ValueError("cutoff must be timezone-aware")
    normalized_cutoff = cutoff.astimezone(UTC)
    if normalized_cutoff.second or normalized_cutoff.microsecond:
        raise ValueError("cutoff must be minute-aligned")
    ordered_envelopes = tuple(
        sorted(envelopes, key=lambda item: (item.symbol, item.timeframe, item.start_ms))
    )
    ordered_units = tuple(
        sorted(work_units, key=lambda item: (item.symbol, item.timeframe, item.start_ms))
    )
    values: dict[str, Any] = {
        "run_id": run_id,
        "cutoff": normalized_cutoff.isoformat().replace("+00:00", "Z"),
        "dump_sha256": dump_sha256.lower(),
        "source_row_count": source_row_count,
        "mapping_version": mapping_version,
        "candidate_venue": candidate_venue,
        "market_type": market_type,
        "source_revision": source_revision,
        "algorithm_version": algorithm_version,
        "code_commit": code_commit.lower(),
        "uv_lock_sha256": uv_lock_sha256.lower(),
        "envelopes": ordered_envelopes,
        "work_units": ordered_units,
    }
    provisional = ReconciliationRunManifest.__new__(ReconciliationRunManifest)
    for name, value in values.items():
        object.__setattr__(provisional, name, value)
    object.__setattr__(provisional, "manifest_sha256", "0" * 64)
    digest = hashlib.sha256(_canonical_json(provisional.logical_dict())).hexdigest()
    return ReconciliationRunManifest(**values, manifest_sha256=digest)


def work_unit_manifest_from_dict(raw: Mapping[str, object]) -> WorkUnitManifest:
    """Parse and checksum-validate one serialized work-unit manifest."""
    classification_counts = _mapping_counts(raw["classification_counts"])
    differing_counts = _mapping_counts(raw["differing_field_counts"])
    return WorkUnitManifest(
        run_id=str(raw["run_id"]),
        work_unit_id=str(raw["work_unit_id"]),
        publication_path=str(raw["publication_path"]),
        row_count=int(str(raw["row_count"])),
        classification_counts=classification_counts,
        differing_field_counts=differing_counts,
        source_artifacts=tuple(
            _source_artifact_from_dict(item) for item in _sequence(raw["source_artifacts"])
        ),
        replacement_row_count=int(str(raw["replacement_row_count"])),
        replacement_logical_sha256=str(raw["replacement_logical_sha256"]),
        max_rows_per_part=int(str(raw["max_rows_per_part"])),
        max_buffered_rows=int(str(raw["max_buffered_rows"])),
        status=str(raw["status"]),  # type: ignore[arg-type]
        parts=tuple(
            LedgerPart(**dict(item))  # type: ignore[arg-type]
            for item in _sequence(raw["parts"])
        ),
        manifest_sha256=str(raw["manifest_sha256"]),
    )


def read_reconciliation_run(path: Path) -> ReconciliationRunManifest:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError("reconciliation run manifest is not valid JSON") from error
    if not isinstance(raw, Mapping):
        raise ValueError("reconciliation run manifest must be a JSON object")
    supplied = raw.get("manifest_sha256")
    logical = {key: value for key, value in raw.items() if key != "manifest_sha256"}
    actual = hashlib.sha256(_canonical_json(logical)).hexdigest()
    if supplied != actual:
        raise ValueError("reconciliation run manifest checksum does not match its content")
    return ReconciliationRunManifest(
        run_id=str(raw["run_id"]),
        cutoff=str(raw["cutoff"]),
        dump_sha256=str(raw["dump_sha256"]),
        source_row_count=int(str(raw["source_row_count"])),
        mapping_version=str(raw["mapping_version"]),
        candidate_venue=str(raw["candidate_venue"]),
        market_type=str(raw["market_type"]),
        source_revision=str(raw["source_revision"]),
        algorithm_version=str(raw["algorithm_version"]),
        code_commit=str(raw["code_commit"]),
        uv_lock_sha256=str(raw["uv_lock_sha256"]),
        envelopes=tuple(
            TradingEnvelope(**dict(item))  # type: ignore[arg-type]
            for item in _sequence(raw["envelopes"])
        ),
        work_units=tuple(
            ReconciliationWorkUnit(**dict(item))  # type: ignore[arg-type]
            for item in _sequence(raw["work_units"])
        ),
        manifest_sha256=str(supplied),
    )


def write_reconciliation_run(run: ReconciliationRunManifest, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if read_reconciliation_run(path) != run:
            raise ValueError("immutable reconciliation run path contains different content")
        return
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(run.to_json(), encoding="utf-8")
    os.replace(temporary, path)


def _mapping_counts(value: object) -> tuple[tuple[str, int], ...]:
    if not isinstance(value, Mapping):
        raise ValueError("manifest counts must be a JSON object")
    return tuple(sorted((str(key), int(str(count))) for key, count in value.items()))


def _source_artifact_dict(item: SourceArtifactIdentity) -> dict[str, object]:
    value: dict[str, object] = {
        "location": item.location,
        "payload_sha256": item.payload_sha256,
        "published_sha256": item.published_sha256,
        "source_revision": item.source_revision,
        "retrieved_at": item.retrieved_at,
    }
    if item.excluded_row_count:
        value["excluded_row_count"] = item.excluded_row_count
    if item.integrity_notes:
        value["integrity_notes"] = list(item.integrity_notes)
    return value


def _source_artifact_from_dict(raw: Mapping[str, object]) -> SourceArtifactIdentity:
    notes = raw.get("integrity_notes", ())
    if not isinstance(notes, (list, tuple)) or not all(isinstance(note, str) for note in notes):
        raise ValueError("source integrity notes must be a string list")
    return SourceArtifactIdentity(
        location=str(raw["location"]),
        payload_sha256=str(raw["payload_sha256"]),
        published_sha256=(
            None if raw.get("published_sha256") is None else str(raw["published_sha256"])
        ),
        source_revision=str(raw["source_revision"]),
        retrieved_at=str(raw["retrieved_at"]),
        excluded_row_count=int(str(raw.get("excluded_row_count", 0))),
        integrity_notes=tuple(notes),
    )


def _sequence(value: object) -> Sequence[Mapping[str, object]]:
    if not isinstance(value, list) or not all(isinstance(item, Mapping) for item in value):
        raise ValueError("manifest records must be JSON object lists")
    return value  # type: ignore[return-value]


def _require_sorted_counts(value: tuple[tuple[str, int], ...], name: str) -> None:
    if value != tuple(sorted(value)) or len(value) != len({key for key, _ in value}):
        raise ValueError(f"{name} must have unique canonical order")
    if any(not key or count < 0 for key, count in value):
        raise ValueError(f"{name} contains an invalid key or count")


def _require_sha(value: str, name: str) -> None:
    if _SHA256.fullmatch(value) is None:
        raise ValueError(f"{name} must be a lowercase SHA-256 digest")


def _require_component(value: str, name: str) -> None:
    if _COMPONENT.fullmatch(value) is None:
        raise ValueError(f"{name} must be a stable non-empty identifier")


def _parse_utc(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError("timestamp must be ISO-8601 UTC") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("timestamp must be timezone-aware")
    return parsed.astimezone(UTC)


def _canonical_json(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


def _pretty_json(value: object) -> str:
    return json.dumps(value, indent=2, sort_keys=True, separators=(",", ": ")) + "\n"


__all__ = [
    "MAX_LEDGER_ROWS_PER_PART",
    "LedgerPart",
    "ReconciliationRunManifest",
    "SourceArtifactIdentity",
    "TradingEnvelope",
    "WorkUnitManifest",
    "freeze_reconciliation_run",
    "read_reconciliation_run",
    "write_reconciliation_run",
    "work_unit_manifest_from_dict",
]
