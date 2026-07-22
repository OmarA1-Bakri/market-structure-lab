"""Bounded deterministic aggregation of complete canonical one-minute bars."""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat
import struct
from typing import Any, Final, cast

import polars as pl

from market_structure_lab.core.identity import canonical_json, hash_json
from market_structure_lab.core.artifact_io import require_regular_directory
from market_structure_lab.core.secure_windows import (
    WindowsHandleFilesystem,
    validated_relative_parts,
)
from market_structure_lab.data.canonical import (
    CANONICAL_COLUMNS,
    CANONICAL_SCHEMA,
    timeframe_microseconds,
    validate_candle_values,
)
from market_structure_lab.research.models import (
    ValidationWorkBudget,
    ValidationWorkBudgetViolation,
    ValidationWorkDemand,
)

SOURCE_TIMEFRAME: Final = "1m"
SUPPORTED_TARGET_TIMEFRAMES: Final = ("15m", "1h", "4h")
CONTINUITY_ID: Final = "complete-contiguous-1m-v1"
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_SOURCE_SCHEMA = pl.Schema(cast(Any, {**CANONICAL_SCHEMA, "segment_id": pl.UInt64}))
_MAX_SPOOL_LINE_BYTES: Final = 128 * 1024
_MAX_AUTHENTICATED_SPOOL_LINE_BYTES: Final = _MAX_SPOOL_LINE_BYTES + 1024
_SPOOL_CHUNK_BYTES: Final = 1024 * 1024
_SPOOL_CHAIN_BYTES: Final = 32


class OrderedSourceIdentity:
    """Streaming domain-separated identity for ordered canonical source-row IDs."""

    __slots__ = ("_count", "_state")

    def __init__(self) -> None:
        self._count = 0
        self._state = hash_json(
            "canonical-source-minute-stream-start",
            {"source_timeframe": SOURCE_TIMEFRAME},
        )

    @property
    def count(self) -> int:
        return self._count

    def update(self, source_row_id: str) -> None:
        _require_sha256(source_row_id, "source row identity")
        self._state = hash_json(
            "canonical-source-minute-stream-step",
            {
                "index": self._count,
                "previous_sha256": self._state,
                "source_row_sha256": source_row_id,
            },
        )
        self._count += 1

    def hexdigest(self) -> str:
        return hash_json(
            "canonical-source-minute-stream-final",
            {"count": self._count, "ordered_chain_sha256": self._state},
        )


def canonical_source_row_payload(row: Mapping[str, object]) -> dict[str, object]:
    """Return the exact schema-versioned logical payload for one canonical source minute."""

    if set(row) != {*CANONICAL_COLUMNS, "segment_id"}:
        raise ValueError("canonical source row schema is invalid")
    validate_candle_values(row)
    segment_id = row["segment_id"]
    if isinstance(segment_id, bool) or not isinstance(segment_id, int) or segment_id < 0:
        raise ValueError("canonical source row segment_id must be a non-negative integer")
    timestamp = cast(datetime, row["timestamp"]).astimezone(UTC)
    return {
        "schema_version": 1,
        "timestamp": timestamp,
        "symbol": row["symbol"],
        "timeframe": row["timeframe"],
        "open": cast(float, row["open"]),
        "high": cast(float, row["high"]),
        "low": cast(float, row["low"]),
        "close": cast(float, row["close"]),
        "volume": cast(float, row["volume"]),
        "segment_id": segment_id,
    }


def canonical_source_row_identity(row: Mapping[str, object]) -> str:
    """Return a domain-separated identity for one exact canonical source minute."""

    return hash_json("canonical-source-minute", canonical_source_row_payload(row))


def source_rows_sha256(
    rows: Iterable[Mapping[str, object] | str],
    *,
    identities: bool = False,
) -> str:
    """Return a bounded-memory digest of ordered source rows or their identities."""

    accumulator = OrderedSourceIdentity()
    for row in rows:
        if identities:
            if not isinstance(row, str):
                raise TypeError("source identity stream must contain strings")
            source_row_id = row
        else:
            if not isinstance(row, Mapping):
                raise TypeError("source row stream must contain mappings")
            source_row_id = canonical_source_row_identity(row)
        accumulator.update(source_row_id)
    return accumulator.hexdigest()


@dataclass(frozen=True, slots=True)
class CanonicalAggregateBar:
    """One complete target bar bound to every ordered one-minute source identity."""

    schema_version: int
    timestamp: datetime
    bar_close: datetime
    symbol: str
    source_timeframe: str
    target_timeframe: str
    segment_id: int
    continuity: str
    open: float
    high: float
    low: float
    close: float
    volume: float
    source_row_count: int
    source_row_ids: tuple[str, ...]
    source_sha256: str
    parent_snapshot_sha256: str
    row_sha256: str = ""

    def __post_init__(self) -> None:
        if self.schema_version != 1:
            raise ValueError("unsupported aggregate row schema")
        if self.source_timeframe != SOURCE_TIMEFRAME:
            raise ValueError("aggregate source timeframe must be 1m")
        target_minutes = target_timeframe_minutes(self.target_timeframe)
        timestamp = _require_utc(self.timestamp, "aggregate timestamp")
        bar_close = _require_utc(self.bar_close, "aggregate bar_close")
        if timestamp != self.timestamp or bar_close != self.bar_close:
            raise ValueError("aggregate timestamps must use UTC")
        if bar_close != timestamp + timedelta(minutes=target_minutes):
            raise ValueError("aggregate bar_close must be exactly one target period after open")
        if int(timestamp.timestamp() * 1_000_000) % timeframe_microseconds(self.target_timeframe):
            raise ValueError("aggregate timestamp is not aligned to target timeframe")
        validate_candle_values(
            {
                "timestamp": timestamp,
                "symbol": self.symbol,
                "timeframe": self.target_timeframe,
                "open": self.open,
                "high": self.high,
                "low": self.low,
                "close": self.close,
                "volume": self.volume,
            }
        )
        if isinstance(self.segment_id, bool) or self.segment_id < 0:
            raise ValueError("aggregate segment_id must be a non-negative integer")
        if self.continuity != CONTINUITY_ID:
            raise ValueError("aggregate continuity identity is unsupported")
        if self.source_row_count != target_minutes:
            raise ValueError("aggregate source row count does not complete target period")
        if len(self.source_row_ids) != self.source_row_count:
            raise ValueError("aggregate source identity count mismatch")
        for source_row_id in self.source_row_ids:
            _require_sha256(source_row_id, "source row identity")
        expected_source_sha256 = source_rows_sha256(self.source_row_ids, identities=True)
        if self.source_sha256 != expected_source_sha256:
            raise ValueError("aggregate source digest mismatch")
        _require_sha256(self.parent_snapshot_sha256, "parent snapshot sha256")
        expected_row_sha256 = hash_json("canonical-aggregate-bar", self.logical_dict())
        if self.row_sha256 and self.row_sha256 != expected_row_sha256:
            raise ValueError("aggregate row identity mismatch")
        object.__setattr__(self, "row_sha256", expected_row_sha256)

    def logical_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "timestamp": self.timestamp,
            "bar_close": self.bar_close,
            "symbol": self.symbol,
            "source_timeframe": self.source_timeframe,
            "target_timeframe": self.target_timeframe,
            "segment_id": self.segment_id,
            "continuity": self.continuity,
            "open": self.open,
            "high": self.high,
            "low": self.low,
            "close": self.close,
            "volume": self.volume,
            "source_row_count": self.source_row_count,
            "source_row_ids": self.source_row_ids,
            "source_sha256": self.source_sha256,
            "parent_snapshot_sha256": self.parent_snapshot_sha256,
        }

    def to_dict(self) -> dict[str, object]:
        return {
            **self.logical_dict(),
            "timestamp": _iso_utc(self.timestamp),
            "bar_close": _iso_utc(self.bar_close),
            "source_row_ids": list(self.source_row_ids),
            "row_sha256": self.row_sha256,
        }

    def to_json_line(self) -> bytes:
        return (json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":")) + "\n").encode(
            "utf-8"
        )

    @classmethod
    def from_mapping(cls, row: Mapping[str, object]) -> CanonicalAggregateBar:
        expected = {
            "schema_version",
            "timestamp",
            "bar_close",
            "symbol",
            "source_timeframe",
            "target_timeframe",
            "segment_id",
            "continuity",
            "open",
            "high",
            "low",
            "close",
            "volume",
            "source_row_count",
            "source_row_ids",
            "source_sha256",
            "parent_snapshot_sha256",
            "row_sha256",
        }
        if set(row) != expected:
            raise ValueError("aggregate row schema is invalid")
        source_ids = row["source_row_ids"]
        if not isinstance(source_ids, (list, tuple)):
            raise ValueError("aggregate source_row_ids must be an ordered sequence")
        return cls(
            schema_version=int(cast(Any, row["schema_version"])),
            timestamp=_coerce_datetime(row["timestamp"]),
            bar_close=_coerce_datetime(row["bar_close"]),
            symbol=str(row["symbol"]),
            source_timeframe=str(row["source_timeframe"]),
            target_timeframe=str(row["target_timeframe"]),
            segment_id=int(cast(Any, row["segment_id"])),
            continuity=str(row["continuity"]),
            open=float(cast(Any, row["open"])),
            high=float(cast(Any, row["high"])),
            low=float(cast(Any, row["low"])),
            close=float(cast(Any, row["close"])),
            volume=float(cast(Any, row["volume"])),
            source_row_count=int(cast(Any, row["source_row_count"])),
            source_row_ids=tuple(str(value) for value in source_ids),
            source_sha256=str(row["source_sha256"]),
            parent_snapshot_sha256=str(row["parent_snapshot_sha256"]),
            row_sha256=str(row["row_sha256"]),
        )


@dataclass(frozen=True, slots=True)
class VerifiedAggregateBarSpool:
    """Receipt for a fully validated, bounded on-disk aggregate-bar stream."""

    trusted_root: Path
    relative_path: Path
    artifact_sha256: str
    artifact_bytes: int
    bar_count: int
    chain_root_sha256: str
    target_timeframe: str
    source_sha256: str
    parent_snapshot_sha256: str
    max_line_bytes: int = _MAX_AUTHENTICATED_SPOOL_LINE_BYTES

    def __post_init__(self) -> None:
        if not isinstance(self.trusted_root, Path) or not isinstance(self.relative_path, Path):
            raise TypeError("aggregate spool paths must be Path values")
        validated_relative_parts(self.relative_path)
        _require_sha256(self.artifact_sha256, "aggregate spool sha256")
        _require_sha256(self.chain_root_sha256, "aggregate spool chain root sha256")
        _require_sha256(self.source_sha256, "aggregate spool source sha256")
        _require_sha256(self.parent_snapshot_sha256, "aggregate spool parent snapshot sha256")
        target_timeframe_minutes(self.target_timeframe)
        if isinstance(self.artifact_bytes, bool) or self.artifact_bytes < 0:
            raise ValueError("aggregate spool artifact_bytes must be non-negative")
        if isinstance(self.bar_count, bool) or self.bar_count < 0:
            raise ValueError("aggregate spool bar_count must be non-negative")
        if (
            isinstance(self.max_line_bytes, bool)
            or self.max_line_bytes < 1
            or self.max_line_bytes > _MAX_AUTHENTICATED_SPOOL_LINE_BYTES
        ):
            raise ValueError("aggregate spool line bound is invalid")

    @property
    def path(self) -> Path:
        return self.trusted_root / self.relative_path


def target_timeframe_minutes(target_timeframe: str) -> int:
    if target_timeframe not in SUPPORTED_TARGET_TIMEFRAMES:
        raise ValueError(
            "target timeframe must be one of the supported integral values: 15m, 1h, 4h"
        )
    target_microseconds = timeframe_microseconds(target_timeframe)
    source_microseconds = timeframe_microseconds(SOURCE_TIMEFRAME)
    quotient, remainder = divmod(target_microseconds, source_microseconds)
    if remainder or quotient < 1:
        raise ValueError("target timeframe must be an integral multiple of 1m")
    return quotient


def spool_complete_aggregate_bars(
    batches: Iterable[pl.DataFrame],
    *,
    spool_root: str | Path,
    spool_relative_path: str | Path,
    target_timeframe: str,
    expected_source_sha256: str,
    parent_snapshot_sha256: str,
    demand: ValidationWorkDemand,
    budget: ValidationWorkBudget,
) -> VerifiedAggregateBarSpool:
    """Build an authenticated spool and return only after terminal source verification."""

    root = Path(spool_root).absolute()
    relative = Path(*validated_relative_parts(spool_relative_path))
    require_regular_directory(root)
    if not isinstance(demand, ValidationWorkDemand):
        raise TypeError("demand must be a ValidationWorkDemand")
    if not isinstance(budget, ValidationWorkBudget):
        raise TypeError("budget must be a ValidationWorkBudget")
    budget.preflight(demand, deferred_work=batches)
    temporary_paths = (
        relative.with_name(f".{relative.name}.payload.partial"),
        relative.with_name(f".{relative.name}.offsets.partial"),
        relative.with_name(f".{relative.name}.chains.partial"),
    )
    created: list[Path] = []
    payload_fd: int | None = None
    offsets_fd: int | None = None
    chains_fd: int | None = None
    output_fd: int | None = None
    maximum_spool_bytes = min(
        budget.max_source_bytes,
        demand.source_bytes,
        demand.aggregate_bars * _MAX_AUTHENTICATED_SPOOL_LINE_BYTES,
    )
    try:
        payload_fd = _create_secure_regular(root, temporary_paths[0])
        created.append(temporary_paths[0])
        offsets_fd = _create_secure_regular(root, temporary_paths[1])
        created.append(temporary_paths[1])
        payload_bytes = 0
        bar_count = 0
        for bar in _iter_provisional_aggregate_bars(
            batches,
            target_timeframe=target_timeframe,
            expected_source_sha256=expected_source_sha256,
            parent_snapshot_sha256=parent_snapshot_sha256,
            demand=demand,
            budget=budget,
        ):
            line = bar.to_json_line()
            if len(line) > _MAX_SPOOL_LINE_BYTES + 1:
                raise RuntimeError("aggregate spool record exceeds its bounded width")
            if payload_bytes + len(line) > maximum_spool_bytes:
                raise ValidationWorkBudgetViolation(
                    "aggregate_spool_bytes",
                    payload_bytes + len(line),
                    maximum_spool_bytes,
                )
            _write_all(offsets_fd, struct.pack("<Q", payload_bytes))
            _write_all(payload_fd, line)
            payload_bytes += len(line)
            bar_count += 1
        os.fsync(payload_fd)
        os.fsync(offsets_fd)

        chains_fd = _create_secure_regular(root, temporary_paths[2])
        created.append(temporary_paths[2])
        os.ftruncate(chains_fd, bar_count * _SPOOL_CHAIN_BYTES)
        chain_end = _spool_chain_end(
            bar_count=bar_count,
            target_timeframe=target_timeframe,
            source_sha256=expected_source_sha256,
            parent_snapshot_sha256=parent_snapshot_sha256,
        )
        chain = chain_end
        for index in range(bar_count - 1, -1, -1):
            payload = _read_indexed_payload(
                payload_fd,
                offsets_fd,
                index=index,
                bar_count=bar_count,
                payload_bytes=payload_bytes,
            )
            chain = _spool_record_chain(
                index=index,
                payload_sha256=hashlib.sha256(payload).hexdigest(),
                next_sha256=chain,
            )
            _pwrite_all(chains_fd, bytes.fromhex(chain), index * _SPOOL_CHAIN_BYTES)
        os.fsync(chains_fd)

        output_fd = _create_secure_regular(root, relative)
        created.append(relative)
        artifact_digest = hashlib.sha256()
        artifact_bytes = 0
        for index in range(bar_count):
            payload = _read_indexed_payload(
                payload_fd,
                offsets_fd,
                index=index,
                bar_count=bar_count,
                payload_bytes=payload_bytes,
            )
            next_sha256 = (
                _pread_exact(chains_fd, _SPOOL_CHAIN_BYTES, (index + 1) * _SPOOL_CHAIN_BYTES).hex()
                if index + 1 < bar_count
                else chain_end
            )
            envelope = _authenticated_spool_record(
                index=index,
                payload=cast(dict[str, object], json.loads(payload)),
                next_sha256=next_sha256,
            )
            if len(envelope) > _MAX_AUTHENTICATED_SPOOL_LINE_BYTES + 1:
                raise RuntimeError("authenticated aggregate spool record exceeds its bound")
            artifact_bytes += len(envelope)
            if artifact_bytes > maximum_spool_bytes:
                raise ValidationWorkBudgetViolation(
                    "aggregate_spool_bytes",
                    artifact_bytes,
                    maximum_spool_bytes,
                )
            _write_all(output_fd, envelope)
            artifact_digest.update(envelope)
        os.fsync(output_fd)
        for descriptor in (output_fd, chains_fd, offsets_fd, payload_fd):
            os.close(descriptor)
        output_fd = chains_fd = offsets_fd = payload_fd = None
        for temporary in temporary_paths:
            _unlink_secure_regular(root, temporary)
            created.remove(temporary)
        return VerifiedAggregateBarSpool(
            trusted_root=root,
            relative_path=relative,
            artifact_sha256=artifact_digest.hexdigest(),
            artifact_bytes=artifact_bytes,
            bar_count=bar_count,
            chain_root_sha256=chain,
            target_timeframe=target_timeframe,
            source_sha256=expected_source_sha256,
            parent_snapshot_sha256=parent_snapshot_sha256,
            max_line_bytes=_MAX_AUTHENTICATED_SPOOL_LINE_BYTES,
        )
    except Exception:
        for cleanup_descriptor in (output_fd, chains_fd, offsets_fd, payload_fd):
            if cleanup_descriptor is not None:
                os.close(cleanup_descriptor)
        for created_path in reversed(created):
            try:
                _unlink_secure_regular(root, created_path)
            except FileNotFoundError:
                pass
        raise


def iter_complete_aggregate_bars(
    spool: VerifiedAggregateBarSpool,
) -> Iterator[CanonicalAggregateBar]:
    """Authenticate each record against the frozen chain immediately before yielding it."""

    if not isinstance(spool, VerifiedAggregateBarSpool):
        raise TypeError("spool must be a VerifiedAggregateBarSpool")
    descriptor = _open_secure_regular(spool.trusted_root, spool.relative_path)
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_size != spool.artifact_bytes:
            raise RuntimeError("aggregate spool is not the recorded regular file")
        digest = hashlib.sha256()
        while chunk := os.read(descriptor, _SPOOL_CHUNK_BYTES):
            digest.update(chunk)
        if digest.hexdigest() != spool.artifact_sha256:
            raise ValueError("aggregate spool checksum mismatch")
        os.lseek(descriptor, 0, os.SEEK_SET)
        _spool_pre_yield_hook(spool)
        expected_chain = spool.chain_root_sha256
        count = 0
        source_identity = OrderedSourceIdentity()
        series_key: tuple[str, str, int] | None = None
        previous_close: datetime | None = None
        with os.fdopen(descriptor, "rb", closefd=False) as handle:
            while line := handle.readline(spool.max_line_bytes + 2):
                if len(line) > spool.max_line_bytes + 1 or not line.endswith(b"\n"):
                    raise RuntimeError(
                        "authenticated aggregate spool record exceeds its bound or lacks a newline"
                    )
                if count >= spool.bar_count:
                    raise ValueError("aggregate spool contains more rows than its receipt")
                envelope = json.loads(line)
                if not isinstance(envelope, dict) or set(envelope) != {
                    "schema_version",
                    "index",
                    "payload",
                    "payload_sha256",
                    "next_sha256",
                }:
                    raise ValueError("aggregate spool envelope schema is invalid")
                if envelope["schema_version"] != 1 or envelope["index"] != count:
                    raise ValueError("aggregate spool envelope order is invalid")
                payload = envelope["payload"]
                if not isinstance(payload, dict):
                    raise ValueError("aggregate spool payload is invalid")
                bar = CanonicalAggregateBar.from_mapping(payload)
                payload_bytes = bar.to_json_line()
                payload_sha256 = hashlib.sha256(payload_bytes).hexdigest()
                if payload != bar.to_dict() or envelope["payload_sha256"] != payload_sha256:
                    raise ValueError("aggregate spool payload authentication failed")
                next_sha256 = envelope["next_sha256"]
                _require_sha256(next_sha256, "aggregate spool next chain sha256")
                if line != _authenticated_spool_record(
                    index=count,
                    payload=bar.to_dict(),
                    next_sha256=next_sha256,
                ):
                    raise ValueError("aggregate spool envelope is not exact canonical JSON")
                actual_chain = _spool_record_chain(
                    index=count,
                    payload_sha256=payload_sha256,
                    next_sha256=next_sha256,
                )
                if actual_chain != expected_chain:
                    raise ValueError("aggregate spool record chain authentication failed")
                current_key = (bar.symbol, bar.source_timeframe, bar.segment_id)
                if series_key is None:
                    series_key = current_key
                elif current_key != series_key or bar.timestamp != previous_close:
                    raise ValueError("aggregate spool records are not one contiguous source series")
                if (
                    bar.target_timeframe != spool.target_timeframe
                    or bar.parent_snapshot_sha256 != spool.parent_snapshot_sha256
                ):
                    raise ValueError("aggregate spool record metadata differs from its receipt")
                previous_close = bar.bar_close
                for source_row_id in bar.source_row_ids:
                    source_identity.update(source_row_id)
                count += 1
                expected_chain = next_sha256
                yield bar
        expected_end = _spool_chain_end(
            bar_count=spool.bar_count,
            target_timeframe=spool.target_timeframe,
            source_sha256=spool.source_sha256,
            parent_snapshot_sha256=spool.parent_snapshot_sha256,
        )
        if count != spool.bar_count or expected_chain != expected_end:
            raise ValueError("aggregate spool chain did not complete against its receipt")
        if source_identity.hexdigest() != spool.source_sha256:
            raise ValueError("aggregate spool source digest differs from its receipt")
    finally:
        os.close(descriptor)


def _iter_provisional_aggregate_bars(
    batches: Iterable[pl.DataFrame],
    *,
    target_timeframe: str,
    expected_source_sha256: str,
    parent_snapshot_sha256: str,
    demand: ValidationWorkDemand,
    budget: ValidationWorkBudget,
) -> Iterator[CanonicalAggregateBar]:
    """Generate provisional bars whose stream identity is checked only at exhaustion."""

    target_minutes = target_timeframe_minutes(target_timeframe)
    _require_sha256(expected_source_sha256, "expected source sha256")
    _require_sha256(parent_snapshot_sha256, "parent snapshot sha256")
    if not isinstance(demand, ValidationWorkDemand):
        raise TypeError("demand must be a ValidationWorkDemand")
    if not isinstance(budget, ValidationWorkBudget):
        raise TypeError("budget must be a ValidationWorkBudget")
    budget.preflight(demand, deferred_work=batches)

    source_stream = OrderedSourceIdentity()
    current_rows: list[dict[str, object]] = []
    current_ids: list[str] = []
    previous_timestamp: datetime | None = None
    first_key: tuple[str, str, int] | None = None
    actual_source_bytes = 0
    aggregate_count = 0

    for batch in batches:
        _validate_source_frame_schema(batch)
        for raw_row in batch.iter_rows(named=True):
            row = canonical_source_row_payload(raw_row)
            timestamp = cast(datetime, row["timestamp"])
            series_key = (
                cast(str, row["symbol"]),
                cast(str, row["timeframe"]),
                cast(int, row["segment_id"]),
            )
            if series_key[1] != SOURCE_TIMEFRAME:
                raise ValueError("aggregate source timeframe must remain 1m")
            if first_key is None:
                first_key = series_key
                if int(timestamp.timestamp() * 1_000_000) % timeframe_microseconds(
                    target_timeframe
                ):
                    raise ValueError("first source minute is not aligned to target timeframe")
            elif series_key != first_key:
                raise ValueError("mixed symbol, source timeframe, or segment in aggregate input")
            if previous_timestamp is not None:
                expected_timestamp = previous_timestamp + timedelta(minutes=1)
                if timestamp == previous_timestamp:
                    raise ValueError("duplicate canonical source minute")
                if timestamp < expected_timestamp:
                    raise ValueError("reordered or out-of-order canonical source minute")
                if timestamp > expected_timestamp:
                    raise ValueError("gap in canonical source minutes; filling is prohibited")
            previous_timestamp = timestamp

            source_row_id = canonical_source_row_identity(raw_row)
            encoded_bytes = len(canonical_json("canonical-source-minute", row))
            source_stream.update(source_row_id)
            actual_source_bytes += encoded_bytes
            _require_within_declared(
                "source_rows", source_stream.count, demand.source_rows, budget.max_source_rows
            )
            _require_within_declared(
                "source_bytes",
                actual_source_bytes,
                demand.source_bytes,
                budget.max_source_bytes,
            )
            current_rows.append(row)
            current_ids.append(source_row_id)
            if len(current_rows) == target_minutes:
                aggregate_count += 1
                _require_within_declared(
                    "aggregate_bars",
                    aggregate_count,
                    demand.aggregate_bars,
                    budget.max_aggregate_bars,
                )
                yield _aggregate_rows(
                    current_rows,
                    tuple(current_ids),
                    target_timeframe=target_timeframe,
                    parent_snapshot_sha256=parent_snapshot_sha256,
                )
                current_rows = []
                current_ids = []

    if current_rows:
        raise ValueError("partial final target period is prohibited")
    if source_stream.hexdigest() != expected_source_sha256:
        raise ValueError("source digest mismatch")


def _authenticated_spool_record(
    *,
    index: int,
    payload: dict[str, object],
    next_sha256: str,
) -> bytes:
    payload_bytes = CanonicalAggregateBar.from_mapping(payload).to_json_line()
    return (
        json.dumps(
            {
                "schema_version": 1,
                "index": index,
                "payload": payload,
                "payload_sha256": hashlib.sha256(payload_bytes).hexdigest(),
                "next_sha256": next_sha256,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")


def _spool_record_chain(*, index: int, payload_sha256: str, next_sha256: str) -> str:
    return hash_json(
        "aggregate-spool-record-chain-v1",
        {
            "index": index,
            "payload_sha256": payload_sha256,
            "next_sha256": next_sha256,
        },
    )


def _spool_chain_end(
    *,
    bar_count: int,
    target_timeframe: str,
    source_sha256: str,
    parent_snapshot_sha256: str,
) -> str:
    return hash_json(
        "aggregate-spool-record-chain-end-v1",
        {
            "bar_count": bar_count,
            "target_timeframe": target_timeframe,
            "source_sha256": source_sha256,
            "parent_snapshot_sha256": parent_snapshot_sha256,
        },
    )


def _read_indexed_payload(
    payload_fd: int,
    offsets_fd: int,
    *,
    index: int,
    bar_count: int,
    payload_bytes: int,
) -> bytes:
    offset = struct.unpack("<Q", _pread_exact(offsets_fd, 8, index * 8))[0]
    next_offset = (
        struct.unpack("<Q", _pread_exact(offsets_fd, 8, (index + 1) * 8))[0]
        if index + 1 < bar_count
        else payload_bytes
    )
    length = next_offset - offset
    if length < 1 or length > _MAX_SPOOL_LINE_BYTES + 1:
        raise RuntimeError("aggregate provisional spool index is invalid")
    payload = _pread_exact(payload_fd, length, offset)
    if not payload.endswith(b"\n"):
        raise RuntimeError("aggregate provisional spool record is incomplete")
    return payload


def _create_secure_regular(trusted_root: Path, relative_path: Path) -> int:
    if _is_windows_platform():
        return WindowsHandleFilesystem().create_regular_descriptor(trusted_root, relative_path)
    parent_fd, name = _open_posix_parent(trusted_root, relative_path)
    try:
        return os.open(
            name,
            os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600,
            dir_fd=parent_fd,
        )
    finally:
        os.close(parent_fd)


def _open_secure_regular(trusted_root: Path, relative_path: Path) -> int:
    if _is_windows_platform():
        return WindowsHandleFilesystem().open_regular_descriptor(trusted_root, relative_path)
    parent_fd, name = _open_posix_parent(trusted_root, relative_path)
    try:
        return os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=parent_fd)
    finally:
        os.close(parent_fd)


def _unlink_secure_regular(trusted_root: Path, relative_path: Path) -> None:
    if _is_windows_platform():
        destination = trusted_root.joinpath(*validated_relative_parts(relative_path))
        with WindowsHandleFilesystem().pin_directory_chain(destination.parent):
            os.unlink(destination)
        return
    parent_fd, name = _open_posix_parent(trusted_root, relative_path)
    try:
        os.unlink(name, dir_fd=parent_fd)
    finally:
        os.close(parent_fd)


def _open_posix_parent(trusted_root: Path, relative_path: Path) -> tuple[int, str]:
    parts = validated_relative_parts(relative_path)
    no_follow = getattr(os, "O_NOFOLLOW", 0)
    directory = getattr(os, "O_DIRECTORY", 0)
    if not no_follow or not directory:
        raise RuntimeError("secure POSIX artifact traversal requires no-follow directory handles")
    descriptor = os.open(trusted_root, os.O_RDONLY | no_follow | directory)
    try:
        for component in parts[:-1]:
            try:
                child = os.open(
                    component,
                    os.O_RDONLY | no_follow | directory,
                    dir_fd=descriptor,
                )
            except OSError as error:
                raise RuntimeError(
                    "secure artifact path contains a symlink, reparse, or invalid ancestor"
                ) from error
            os.close(descriptor)
            descriptor = child
        return descriptor, parts[-1]
    except Exception:
        os.close(descriptor)
        raise


def _is_windows_platform() -> bool:
    return os.name == "nt"


def _spool_pre_yield_hook(spool: VerifiedAggregateBarSpool) -> None:
    """Internal deterministic race seam; production intentionally performs no action."""


def _pread_exact(descriptor: int, length: int, offset: int) -> bytes:
    os.lseek(descriptor, offset, os.SEEK_SET)
    chunks: list[bytes] = []
    remaining = length
    while remaining:
        chunk = os.read(descriptor, remaining)
        if not chunk:
            raise RuntimeError("aggregate spool ended before its bounded record")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def _pwrite_all(descriptor: int, content: bytes, offset: int) -> None:
    os.lseek(descriptor, offset, os.SEEK_SET)
    _write_all(descriptor, content)


def _write_all(descriptor: int, content: bytes) -> None:
    view = memoryview(content)
    while view:
        written = os.write(descriptor, view)
        if written < 1:
            raise OSError("aggregate spool write made no progress")
        view = view[written:]


def _aggregate_rows(
    rows: list[dict[str, object]],
    source_row_ids: tuple[str, ...],
    *,
    target_timeframe: str,
    parent_snapshot_sha256: str,
) -> CanonicalAggregateBar:
    first = rows[0]
    last = rows[-1]
    timestamp = cast(datetime, first["timestamp"])
    return CanonicalAggregateBar(
        schema_version=1,
        timestamp=timestamp,
        bar_close=cast(datetime, last["timestamp"]) + timedelta(minutes=1),
        symbol=cast(str, first["symbol"]),
        source_timeframe=SOURCE_TIMEFRAME,
        target_timeframe=target_timeframe,
        segment_id=cast(int, first["segment_id"]),
        continuity=CONTINUITY_ID,
        open=float(cast(Any, first["open"])),
        high=max(float(cast(Any, row["high"])) for row in rows),
        low=min(float(cast(Any, row["low"])) for row in rows),
        close=float(cast(Any, last["close"])),
        volume=math.fsum(float(cast(Any, row["volume"])) for row in rows),
        source_row_count=len(rows),
        source_row_ids=source_row_ids,
        source_sha256=source_rows_sha256(source_row_ids, identities=True),
        parent_snapshot_sha256=parent_snapshot_sha256,
    )


def _validate_source_frame_schema(frame: pl.DataFrame) -> None:
    if frame.schema != _SOURCE_SCHEMA:
        raise ValueError("canonical aggregate source frame schema is invalid")


def _require_within_declared(stage: str, observed: int, declared: int, limit: int) -> None:
    maximum = min(declared, limit)
    if observed > maximum:
        raise ValidationWorkBudgetViolation(stage, observed, maximum)


def _require_sha256(value: object, label: str) -> None:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise ValueError(f"{label} must be a lowercase SHA-256 hex digest")


def _require_utc(value: datetime, label: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{label} must be timezone-aware")
    return value.astimezone(UTC)


def _coerce_datetime(value: object) -> datetime:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return _require_utc(parsed, "aggregate timestamp")
    raise ValueError("aggregate timestamp is invalid")


def _iso_utc(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


__all__ = [
    "CONTINUITY_ID",
    "CanonicalAggregateBar",
    "OrderedSourceIdentity",
    "SUPPORTED_TARGET_TIMEFRAMES",
    "VerifiedAggregateBarSpool",
    "canonical_source_row_identity",
    "canonical_source_row_payload",
    "iter_complete_aggregate_bars",
    "spool_complete_aggregate_bars",
    "source_rows_sha256",
    "target_timeframe_minutes",
]
