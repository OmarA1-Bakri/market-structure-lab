"""Bounded authentication of development candles against RR-000008 ledgers."""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
import hashlib
from pathlib import Path
import polars as pl

from market_structure_lab.core.artifact_io import (
    bounded_regular_files,
    read_bounded_regular,
    require_regular_directory,
    sha256_regular,
)
from market_structure_lab.core.identity import hash_json
from market_structure_lab.data.reconciliation.publication import (
    read_work_unit_manifest,
)
from market_structure_lab.data.reconciliation.manifests import (
    WorkUnitManifest,
    read_reconciliation_run,
)


_MAX_MANIFEST_BYTES = 4 * 1024 * 1024
_MINUTE_MS = 60_000
_ADMITTED = frozenset({"exact_match", "binance_correction", "binance_fill"})
_ORIGIN_BY_CLASS = {
    "exact_match": "dump_verified_match",
    "binance_correction": "binance_correction",
    "binance_fill": "binance_fill",
}


def _success_marker_matches_manifest(marker: bytes, manifest_sha256: str) -> bool:
    if len(marker) == 65 and marker.endswith(b"\n"):
        digest = marker[:64]
    elif len(marker) == 66 and marker.endswith(b"\r\n"):
        digest = marker[:64]
    else:
        return False
    return digest == manifest_sha256.encode("ascii") and all(
        48 <= byte <= 57 or 97 <= byte <= 102 for byte in digest
    )


@dataclass(frozen=True, slots=True)
class RRLedgerPartV2:
    path: str
    sha256: str
    row_count: int


@dataclass(frozen=True, slots=True)
class RRLedgerWorkUnitV2:
    relative_path: str
    manifest_sha256: str
    work_unit_id: str
    symbol: str
    start_ms: int
    end_ms: int
    parts: tuple[RRLedgerPartV2, ...]


@dataclass(frozen=True, slots=True)
class RRLedgerInventoryV2:
    root: Path
    run_manifest_path: Path
    run_manifest_sha256: str
    frozen_run_inventory_sha256: str
    expected_work_unit_manifest_sha256: tuple[str, ...]
    expected_comparison_part_sha256: tuple[str, ...]
    work_units: tuple[RRLedgerWorkUnitV2, ...]
    inventory_sha256: str

    def verifier_payload(self) -> dict[str, object]:
        return {
            "run_id": "RR-000008",
            "root_path": str(self.root),
            "run_manifest_path": str(self.run_manifest_path),
            "run_manifest_sha256": self.run_manifest_sha256,
            "frozen_run_inventory_sha256": self.frozen_run_inventory_sha256,
            "coverage_authority": {
                "work_unit_manifest_sha256": list(
                    self.expected_work_unit_manifest_sha256
                ),
                "comparison_part_sha256": list(self.expected_comparison_part_sha256),
            },
            "forbidden_part_opens": 0,
            "final_ledger_rows": 0,
            "inventory_sha256": self.inventory_sha256,
            "work_units": [
                {
                    "relative_path": item.relative_path,
                    "manifest_sha256": item.manifest_sha256,
                    "work_unit_id": item.work_unit_id,
                    "symbol": item.symbol,
                    "start_ms": item.start_ms,
                    "end_ms": item.end_ms,
                    "parts": [
                        {
                            "path": part.path,
                            "sha256": part.sha256,
                            "row_count": part.row_count,
                        }
                        for part in item.parts
                    ],
                }
                for item in self.work_units
            ],
            "ledger_semantics": {
                "complete_minute_keyspace": True,
                "admitted_classifications": sorted(_ADMITTED),
                "source_unavailable_emits_candle": False,
                "exact_match_hash": "dump_row_sha256",
                "replacement_hash": "binance_row_sha256",
                "origin_by_classification": dict(sorted(_ORIGIN_BY_CLASS.items())),
            },
        }


@dataclass(frozen=True, slots=True)
class RRLedgerRowV2:
    symbol: str
    timeframe: str
    open_time_ms: int
    classification: str
    expected_row_sha256: str | None
    expected_origin: str | None

    @property
    def admits_candle(self) -> bool:
        return self.classification in _ADMITTED


def verify_rr_ledger_inventory_v2(
    root: Path,
    *,
    symbols: Sequence[str],
    intervals: Sequence[tuple[datetime, datetime]],
    expected_work_unit_manifest_sha256: Sequence[str],
    expected_comparison_part_sha256: Sequence[str],
) -> RRLedgerInventoryV2:
    """Verify every scoped work-unit manifest, marker and part before admission."""

    root = Path(root).resolve(strict=True)
    require_regular_directory(root)
    requested = tuple(sorted(set(symbols)))
    if not requested or any(not item for item in requested):
        raise ValueError("RR ledger symbols are invalid")
    normalized_intervals = tuple(intervals)
    for start, end in normalized_intervals:
        _interval_ms(start, end)
    run_path = root.parent / "RR-000008.run.json"
    run = read_reconciliation_run(run_path)
    if run.run_id != "RR-000008":
        raise ValueError("RR run manifest identity is stale")
    structural: list[dict[str, object]] = []
    parsed_by_id: dict[str, tuple[Path, WorkUnitManifest, str]] = {}
    expected_files: set[str] = set()
    expected_directories: set[str] = set()
    for frozen in run.work_units:
        moment = datetime.fromtimestamp(frozen.start_ms / 1000, tz=UTC)
        relative = (
            Path(f"symbol={frozen.symbol}")
            / f"year={moment.year:04d}"
            / f"month={moment.month:02d}"
        )
        manifest_path = root / relative / "manifest.json"
        expected_directories.update(
            parent.as_posix() for parent in (relative, relative.parent, relative.parent.parent)
        )
        manifest_bytes = read_bounded_regular(manifest_path, _MAX_MANIFEST_BYTES)
        manifest = read_work_unit_manifest(manifest_path)
        if manifest.work_unit_id in parsed_by_id:
            raise ValueError("RR run contains duplicate work-unit metadata")
        parsed_by_id[manifest.work_unit_id] = (
            manifest_path.parent,
            manifest,
            hashlib.sha256(manifest_bytes).hexdigest(),
        )
        expected_files.update(
            {
                (relative / "manifest.json").as_posix(),
                (relative / "_SUCCESS").as_posix(),
                *((relative / part.path).as_posix() for part in manifest.parts),
            }
        )
    actual_files = set(
        bounded_regular_files(
            root,
            maximum=len(expected_files) + len(expected_directories) + 1,
        )
    )
    if actual_files != expected_files:
        raise ValueError("RR frozen run contains missing or unmanifested artifacts")
    expected_by_id = {item.work_unit_id: item for item in run.work_units}
    if set(parsed_by_id) != set(expected_by_id):
        raise ValueError("RR frozen work-unit metadata set is incomplete")
    observed_manifest_hashes = tuple(
        parsed_by_id[work_unit_id][2] for work_unit_id in sorted(parsed_by_id)
    )
    observed_part_hashes = tuple(
        part.sha256
        for work_unit_id in sorted(parsed_by_id)
        for part in parsed_by_id[work_unit_id][1].parts
    ) or (hash_json("phase5-validation-empty-comparison-parts-v2", []),)
    if observed_manifest_hashes != tuple(expected_work_unit_manifest_sha256):
        raise ValueError("RR work-unit manifests differ from frozen coverage authority")
    if observed_part_hashes != tuple(expected_comparison_part_sha256):
        raise ValueError("RR comparison parts differ from frozen coverage authority")
    for work_unit_id in sorted(expected_by_id):
        frozen = expected_by_id[work_unit_id]
        directory, manifest_object, _raw_sha256 = parsed_by_id[work_unit_id]
        manifest = manifest_object
        moment = datetime.fromtimestamp(frozen.start_ms / 1000, tz=UTC)
        expected_relative = (
            Path(f"symbol={frozen.symbol}")
            / f"year={moment.year:04d}"
            / f"month={moment.month:02d}"
        )
        if directory != root / expected_relative:
            raise ValueError("RR frozen work-unit metadata path is substituted")
        inventory_names = bounded_regular_files(directory, maximum=1024)
        expected_names = tuple(
            sorted(("_SUCCESS", "manifest.json", *(p.path for p in manifest.parts)))
        )
        if inventory_names != expected_names:
            raise ValueError("RR frozen work-unit declared inventory differs")
        success = read_bounded_regular(directory / "_SUCCESS", 256)
        if not _success_marker_matches_manifest(success, manifest.manifest_sha256):
            raise ValueError("RR frozen work-unit success marker differs")
        structural.append(
            {
                "work_unit_id": work_unit_id,
                "relative_path": expected_relative.as_posix(),
                "manifest_sha256": manifest.manifest_sha256,
                "parts": [
                    {"path": part.path, "sha256": part.sha256, "row_count": part.row_count}
                    for part in manifest.parts
                ],
            }
        )
    scoped_units = tuple(
        unit
        for unit in run.work_units
        if unit.symbol in requested
        and unit.timeframe == "1m"
        and any(
            unit.start_ms < _interval_ms(start, end)[1]
            and _interval_ms(start, end)[0] < unit.end_ms
            for start, end in normalized_intervals
        )
    )
    if not scoped_units:
        raise ValueError("RR frozen run has no work units for the development scope")
    if any(
        not any(
            _interval_ms(start, end)[0] <= unit.start_ms
            and unit.end_ms <= _interval_ms(start, end)[1]
            for start, end in normalized_intervals
        )
        for unit in scoped_units
    ):
        raise ValueError("RR development boundary does not contain whole frozen work units")
    units: list[RRLedgerWorkUnitV2] = []
    for frozen in scoped_units:
        moment = datetime.fromtimestamp(frozen.start_ms / 1000, tz=UTC)
        relative = (
            Path(f"symbol={frozen.symbol}")
            / f"year={moment.year:04d}"
            / f"month={moment.month:02d}"
        )
        directory = root / relative
        require_regular_directory(directory)
        inventory = bounded_regular_files(directory, maximum=1024)
        if "manifest.json" not in inventory or "_SUCCESS" not in inventory:
            raise ValueError("RR work-unit inventory is incomplete")
        manifest = read_work_unit_manifest(directory / "manifest.json")
        if manifest.run_id != "RR-000008" or manifest.work_unit_id != frozen.work_unit_id:
            raise ValueError("RR work-unit identity is stale")
        if manifest.publication_path != (Path("run_id=RR-000008") / relative).as_posix():
            raise ValueError("RR work-unit publication path is substituted")
        if manifest.row_count != sum(part.row_count for part in manifest.parts):
            raise ValueError("RR work-unit manifest row count differs")
        parts = tuple(
            RRLedgerPartV2(path=part.path, sha256=part.sha256, row_count=part.row_count)
            for part in manifest.parts
        )
        unit = RRLedgerWorkUnitV2(
            relative_path=relative.as_posix(),
            manifest_sha256=manifest.manifest_sha256,
            work_unit_id=manifest.work_unit_id,
            symbol=frozen.symbol,
            start_ms=frozen.start_ms,
            end_ms=frozen.end_ms,
            parts=parts,
        )
        _reopen_unit(directory, unit)
        units.append(unit)
    payload = [_unit_payload(item) for item in units]
    return RRLedgerInventoryV2(
        root=root,
        run_manifest_path=run_path.resolve(strict=True),
        run_manifest_sha256=run.manifest_sha256,
        frozen_run_inventory_sha256=hash_json(
            "phase5-rr-000008-frozen-run-inventory-v2", structural
        ),
        expected_work_unit_manifest_sha256=tuple(expected_work_unit_manifest_sha256),
        expected_comparison_part_sha256=tuple(expected_comparison_part_sha256),
        work_units=tuple(units),
        inventory_sha256=hash_json("phase5-rr-000008-development-ledger-inventory-v2", payload),
    )


def reopen_rr_ledger_inventory_v2(
    payload: object,
    *,
    symbols: Sequence[str],
    intervals: Sequence[tuple[datetime, datetime]],
    expected_work_unit_manifest_sha256: Sequence[str],
    expected_comparison_part_sha256: Sequence[str],
) -> RRLedgerInventoryV2:
    if not isinstance(payload, Mapping):
        raise ValueError("RR ledger verifier payload is invalid")
    root_value = payload.get("root_path")
    expected = payload.get("inventory_sha256")
    if payload.get("run_id") != "RR-000008" or not isinstance(root_value, str):
        raise ValueError("RR ledger verifier identity is invalid")
    inventory = verify_rr_ledger_inventory_v2(
        Path(root_value),
        symbols=symbols,
        intervals=intervals,
        expected_work_unit_manifest_sha256=expected_work_unit_manifest_sha256,
        expected_comparison_part_sha256=expected_comparison_part_sha256,
    )
    if expected != inventory.inventory_sha256 or payload != inventory.verifier_payload():
        raise ValueError("RR ledger verifier inventory changed")
    return inventory


def iter_scoped_rr_ledger_rows_v2(
    inventory: RRLedgerInventoryV2,
    *,
    symbol: str,
    start: datetime,
    end: datetime,
    batch_size: int,
    on_batch: Callable[[int], None] | None = None,
    preflight_verified: bool = False,
) -> Iterator[RRLedgerRowV2]:
    """Stream the complete scoped ledger keyspace using Parquet predicate pushdown."""

    start_ms, end_ms = _interval_ms(start, end)
    if isinstance(batch_size, bool) or not 1 <= batch_size <= 100_000:
        raise ValueError("RR ledger batch size is outside its ceiling")
    if not preflight_verified:
        preflight_scoped_rr_ledger_parts_v2(
            inventory,
            symbol=symbol,
            start=start,
            end=end,
        )
    units = [
        unit
        for unit in inventory.work_units
        if unit.symbol == symbol and _unit_overlaps(unit, start_ms, end_ms)
    ]
    if not units:
        raise ValueError("RR ledger has no work units for the authorized predicate")
    expected_ms = start_ms
    for unit in units:
        directory = inventory.root / unit.relative_path
        for part in unit.parts:
            part_path = directory / part.path
            scan = (
                pl.scan_parquet(part_path)
                .filter(
                    (pl.col("symbol") == symbol)
                    & (pl.col("timeframe") == "1m")
                    & (pl.col("open_time_ms") >= start_ms)
                    & (pl.col("open_time_ms") < end_ms)
                )
                .select(
                    "run_id",
                    "work_unit_id",
                    "symbol",
                    "timeframe",
                    "open_time_ms",
                    "classification",
                    "dump_row_sha256",
                    "binance_row_sha256",
                )
                .sort("open_time_ms")
            )
            for frame in scan.collect_batches(chunk_size=batch_size, engine="streaming"):
                if frame.height > batch_size:
                    raise ValueError("RR ledger exceeded its batch ceiling")
                if on_batch is not None:
                    on_batch(frame.height)
                for raw in frame.iter_rows(named=True):
                    row = _ledger_row(raw, unit)
                    if row.open_time_ms != expected_ms:
                        raise ValueError("RR ledger keys are missing, duplicated, or reordered")
                    expected_ms += _MINUTE_MS
                    yield row
    if expected_ms != end_ms:
        raise ValueError("RR ledger does not cover the complete authorized interval")


def preflight_scoped_rr_ledger_parts_v2(
    inventory: RRLedgerInventoryV2,
    *,
    symbol: str,
    start: datetime,
    end: datetime,
) -> None:
    """Hash every and only development-scoped part before any database iterator."""

    start_ms, end_ms = _interval_ms(start, end)
    units = [
        unit
        for unit in inventory.work_units
        if unit.symbol == symbol and _unit_overlaps(unit, start_ms, end_ms)
    ]
    if not units:
        raise ValueError("RR ledger has no scoped parts for the authorized predicate")
    for unit in units:
        _reopen_unit(inventory.root / unit.relative_path, unit)


def _ledger_row(raw: Mapping[str, object], unit: RRLedgerWorkUnitV2) -> RRLedgerRowV2:
    classification = str(raw["classification"])
    if classification not in {*_ADMITTED, "source_unavailable"}:
        raise ValueError("RR ledger classification is invalid")
    if (
        raw["run_id"] != "RR-000008"
        or raw["work_unit_id"] != unit.work_unit_id
        or raw["symbol"] != unit.symbol
        or raw["timeframe"] != "1m"
    ):
        raise ValueError("RR ledger row has stale or substituted identity")
    dump_hash = raw["dump_row_sha256"]
    binance_hash = raw["binance_row_sha256"]
    expected: object
    if classification == "exact_match":
        expected = dump_hash
    elif classification in {"binance_correction", "binance_fill"}:
        expected = binance_hash
    else:
        expected = None
    if expected is not None and (
        not isinstance(expected, str)
        or len(expected) != 64
        or any(ch not in "0123456789abcdef" for ch in expected)
    ):
        raise ValueError("RR ledger admitted row hash is invalid")
    return RRLedgerRowV2(
        symbol=unit.symbol,
        timeframe="1m",
        open_time_ms=int(str(raw["open_time_ms"])),
        classification=classification,
        expected_row_sha256=expected,
        expected_origin=_ORIGIN_BY_CLASS.get(classification),
    )


def _reopen_unit(directory: Path, expected: RRLedgerWorkUnitV2) -> None:
    require_regular_directory(directory)
    read_bounded_regular(directory / "manifest.json", _MAX_MANIFEST_BYTES)
    manifest = read_work_unit_manifest(directory / "manifest.json")
    if (
        manifest.manifest_sha256 != expected.manifest_sha256
        or manifest.work_unit_id != expected.work_unit_id
        or tuple((item.path, item.sha256, item.row_count) for item in manifest.parts)
        != tuple((item.path, item.sha256, item.row_count) for item in expected.parts)
    ):
        raise ValueError("RR work-unit manifest original bytes changed")
    expected_rows = (expected.end_ms - expected.start_ms) // _MINUTE_MS
    if (
        expected.end_ms <= expected.start_ms
        or (expected.end_ms - expected.start_ms) % _MINUTE_MS
        or manifest.row_count != expected_rows
    ):
        raise ValueError("RR work-unit row count differs from frozen minute count")
    success = read_bounded_regular(directory / "_SUCCESS", 256)
    if not _success_marker_matches_manifest(success, expected.manifest_sha256):
        raise ValueError("RR work-unit success marker changed")
    expected_timestamp = expected.start_ms
    classification_counts: Counter[str] = Counter()
    for part in expected.parts:
        part_path = directory / part.path
        if sha256_regular(part_path) != part.sha256:
            raise ValueError("RR ledger part original bytes changed")
        classification = pl.col("classification")
        valid_row_hash = (
            pl.when(classification == "exact_match")
            .then(pl.col("dump_row_sha256").str.contains(r"^[0-9a-f]{64}$").fill_null(False))
            .when(classification.is_in(("binance_correction", "binance_fill")))
            .then(
                pl.col("binance_row_sha256")
                .str.contains(r"^[0-9a-f]{64}$")
                .fill_null(False)
            )
            .when(classification == "source_unavailable")
            .then(pl.lit(True))
            .otherwise(pl.lit(False))
        )
        summary = (
            pl.scan_parquet(part_path)
            .select(
                pl.len().alias("row_count"),
                pl.col("open_time_ms").min().alias("minimum_open_time_ms"),
                pl.col("open_time_ms").max().alias("maximum_open_time_ms"),
                pl.col("open_time_ms").n_unique().alias("unique_open_time_count"),
                ((pl.col("open_time_ms") - expected.start_ms) % _MINUTE_MS == 0)
                .all()
                .alias("minute_aligned"),
                (pl.col("run_id") == "RR-000008").all().alias("run_id_matches"),
                (pl.col("work_unit_id") == expected.work_unit_id)
                .all()
                .alias("work_unit_matches"),
                (pl.col("symbol") == expected.symbol).all().alias("symbol_matches"),
                (pl.col("timeframe") == "1m").all().alias("timeframe_matches"),
                valid_row_hash.all().alias("row_hashes_valid"),
                *(
                    (classification == value).sum().alias(f"classification_{value}")
                    for value in (*sorted(_ADMITTED), "source_unavailable")
                ),
            )
            .collect(engine="streaming")
            .row(0, named=True)
        )
        physical_rows = int(summary["row_count"])
        if physical_rows != part.row_count:
            raise ValueError("RR ledger part physical row count differs")
        expected_part_end = expected_timestamp + (part.row_count - 1) * _MINUTE_MS
        if (
            summary["minimum_open_time_ms"] != expected_timestamp
            or summary["maximum_open_time_ms"] != expected_part_end
            or summary["unique_open_time_count"] != part.row_count
            or not summary["minute_aligned"]
        ):
            raise ValueError("RR ledger keys are missing, duplicated, or reordered")
        if not all(
            summary[label]
            for label in (
                "run_id_matches",
                "work_unit_matches",
                "symbol_matches",
                "timeframe_matches",
                "row_hashes_valid",
            )
        ):
            raise ValueError("RR ledger row has stale or substituted identity")
        for value in (*sorted(_ADMITTED), "source_unavailable"):
            classification_counts[value] += int(summary[f"classification_{value}"])
        expected_timestamp += part.row_count * _MINUTE_MS
    if expected_timestamp != expected.end_ms:
        raise ValueError("RR ledger does not match the frozen minute keyspace")
    if tuple(sorted((key, value) for key, value in classification_counts.items() if value)) != (
        manifest.classification_counts
    ):
        raise ValueError("RR ledger classifications differ from work-unit manifest")


def _unit_overlaps(unit: RRLedgerWorkUnitV2, start_ms: int, end_ms: int) -> bool:
    return unit.start_ms < end_ms and start_ms < unit.end_ms


def _scoped_months(intervals: Sequence[tuple[datetime, datetime]]) -> tuple[tuple[int, int], ...]:
    months: set[tuple[int, int]] = set()
    for start, end in intervals:
        _interval_ms(start, end)
        cursor = datetime(start.year, start.month, 1, tzinfo=UTC)
        while cursor < end:
            months.add((cursor.year, cursor.month))
            cursor = (
                datetime(cursor.year + 1, 1, 1, tzinfo=UTC)
                if cursor.month == 12
                else datetime(cursor.year, cursor.month + 1, 1, tzinfo=UTC)
            )
    return tuple(sorted(months))


def _interval_ms(start: datetime, end: datetime) -> tuple[int, int]:
    if (
        start.tzinfo is None
        or end.tzinfo is None
        or start.utcoffset() != UTC.utcoffset(start)
        or end.utcoffset() != UTC.utcoffset(end)
        or start.second
        or end.second
        or start.microsecond
        or end.microsecond
        or start >= end
    ):
        raise ValueError("RR ledger scope must be an aligned non-empty UTC interval")
    return int(start.timestamp() * 1000), int(end.timestamp() * 1000)


def _unit_payload(item: RRLedgerWorkUnitV2) -> dict[str, object]:
    return {
        "relative_path": item.relative_path,
        "manifest_sha256": item.manifest_sha256,
        "work_unit_id": item.work_unit_id,
        "symbol": item.symbol,
        "start_ms": item.start_ms,
        "end_ms": item.end_ms,
        "parts": [
            {"path": part.path, "sha256": part.sha256, "row_count": part.row_count}
            for part in item.parts
        ],
    }
