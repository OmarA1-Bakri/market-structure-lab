"""Bounded read-only promotion evidence for a verified reconciliation run."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import polars as pl

from market_structure_lab.data.reconciliation.manifests import (
    ReconciliationRunManifest,
    WorkUnitManifest,
)
from market_structure_lab.data.reconciliation.models import ReconciliationClass
from market_structure_lab.data.reconciliation.publication import sha256_file
from market_structure_lab.data.reconciliation.repository import (
    VerifiedCoverageInterval,
    validate_reconciliation_coverage,
)

MAX_PREFLIGHT_INTERVALS = 100_000
MAX_PREFLIGHT_WORK_UNITS = 10_000


@dataclass(frozen=True, slots=True)
class ReconciliationPromotionPreflight:
    """Verified evidence and exact intended durable effects before promotion."""

    payload: dict[str, Any]
    coverage: tuple[VerifiedCoverageInterval, ...]
    residual_unavailable: tuple[VerifiedCoverageInterval, ...]


@dataclass(slots=True)
class _Breakdown:
    work_units: int = 0
    row_count: int = 0
    replacement_rows: int = 0
    statuses: Counter[str] = field(default_factory=Counter)
    classification_counts: Counter[str] = field(default_factory=Counter)
    differing_field_counts: Counter[str] = field(default_factory=Counter)


def build_reconciliation_promotion_preflight(
    output_root: Path,
    run: ReconciliationRunManifest,
    manifests: tuple[WorkUnitManifest, ...],
    *,
    max_intervals: int = MAX_PREFLIGHT_INTERVALS,
) -> ReconciliationPromotionPreflight:
    """Build a bounded promotion preflight from an already verified exact run publication."""
    if not 1 <= max_intervals <= MAX_PREFLIGHT_INTERVALS:
        raise ValueError("preflight interval limit is outside the safe configured bound")
    if len(run.work_units) > MAX_PREFLIGHT_WORK_UNITS:
        raise ValueError("preflight work-unit inventory exceeds the safe configured bound")
    if len(manifests) != len(run.work_units) or any(
        manifest.work_unit_id != unit.work_unit_id
        for unit, manifest in zip(run.work_units, manifests, strict=True)
    ):
        raise ValueError("promotion preflight requires every frozen work unit in canonical order")

    statuses: Counter[str] = Counter()
    classifications: Counter[str] = Counter()
    differing_fields: Counter[str] = Counter()
    breakdowns: dict[tuple[str, int], _Breakdown] = {}
    replacement_digest = hashlib.sha256()
    replacement_rows = 0
    available: list[VerifiedCoverageInterval] = []
    unavailable: list[VerifiedCoverageInterval] = []

    for unit, manifest in zip(run.work_units, manifests, strict=True):
        statuses[manifest.status] += 1
        classifications.update(dict(manifest.classification_counts))
        differing_fields.update(dict(manifest.differing_field_counts))
        era = datetime.fromtimestamp(unit.start_ms / 1_000, tz=UTC).year
        breakdown = breakdowns.setdefault((unit.symbol, era), _Breakdown())
        breakdown.work_units += 1
        breakdown.row_count += manifest.row_count
        breakdown.replacement_rows += manifest.replacement_row_count
        breakdown.statuses[manifest.status] += 1
        breakdown.classification_counts.update(dict(manifest.classification_counts))
        breakdown.differing_field_counts.update(dict(manifest.differing_field_counts))

        state: str | None = None
        state_start = unit.start_ms
        expected_timestamp = unit.start_ms
        for part in manifest.parts:
            path = output_root / manifest.publication_path / part.path
            if sha256_file(path) != part.sha256:
                raise ValueError("promotion preflight part checksum does not match the manifest")
            frame = pl.read_parquet(
                path,
                columns=[
                    "symbol",
                    "timeframe",
                    "open_time_ms",
                    "classification",
                    "binance_row_sha256",
                ],
            )
            if sha256_file(path) != part.sha256:
                raise ValueError("publication part changed during promotion preflight")
            for symbol, timeframe, timestamp, classification, binance_sha in frame.iter_rows():
                timestamp = int(timestamp)
                if timestamp != expected_timestamp:
                    raise ValueError(
                        "promotion preflight encountered non-contiguous ledger evidence"
                    )
                expected_timestamp += 60_000
                next_state = (
                    "unavailable"
                    if classification == ReconciliationClass.SOURCE_UNAVAILABLE.value
                    else "available"
                )
                if state is None:
                    state = next_state
                    state_start = timestamp
                elif state != next_state:
                    _append_interval(
                        available,
                        unavailable,
                        state=state,
                        symbol=unit.symbol,
                        timeframe=unit.timeframe,
                        start_ms=state_start,
                        end_ms=timestamp,
                        max_intervals=max_intervals,
                    )
                    state = next_state
                    state_start = timestamp
                if classification in (
                    ReconciliationClass.BINANCE_CORRECTION.value,
                    ReconciliationClass.BINANCE_FILL.value,
                ):
                    if binance_sha is None:
                        raise ValueError(
                            "promotion preflight found replacement without Binance hash"
                        )
                    replacement_rows += 1
                    replacement_digest.update(
                        json.dumps(
                            {
                                "binance_row_sha256": str(binance_sha),
                                "open_time_ms": timestamp,
                                "symbol": str(symbol),
                                "timeframe": str(timeframe),
                            },
                            sort_keys=True,
                            separators=(",", ":"),
                        ).encode()
                    )
        if expected_timestamp != unit.end_ms or state is None:
            raise ValueError("promotion preflight did not consume the frozen work unit")
        _append_interval(
            available,
            unavailable,
            state=state,
            symbol=unit.symbol,
            timeframe=unit.timeframe,
            start_ms=state_start,
            end_ms=unit.end_ms,
            max_intervals=max_intervals,
        )

    if replacement_rows != sum(item.replacement_row_count for item in manifests):
        raise ValueError("promotion replacement count differs from verified manifests")
    coverage = validate_reconciliation_coverage(run, _merge_intervals(available))
    residual = _merge_intervals(unavailable)
    work_unit_ids = {item.work_unit_id for item in run.work_units}
    payload: dict[str, Any] = {
        "applied": False,
        "run_id": run.run_id,
        "manifest_sha256": run.manifest_sha256,
        "work_units": len(manifests),
        "work_unit_ids_sha256": _set_sha256(work_unit_ids),
        "work_unit_inventory": [
            {
                "work_unit_id": unit.work_unit_id,
                "manifest_sha256": manifest.manifest_sha256,
                "status": manifest.status,
                "symbol": unit.symbol,
                "timeframe": unit.timeframe,
                "start": _iso_ms(unit.start_ms),
                "end": _iso_ms(unit.end_ms),
                "row_count": manifest.row_count,
                "replacement_rows": manifest.replacement_row_count,
            }
            for unit, manifest in zip(run.work_units, manifests, strict=True)
        ],
        "preflight_bounds": {
            "maximum_intervals": max_intervals,
            "maximum_work_units": MAX_PREFLIGHT_WORK_UNITS,
        },
        "terminal_status_counts": dict(sorted(statuses.items())),
        "row_count": sum(item.row_count for item in manifests),
        "classification_counts": dict(sorted(classifications.items())),
        "differing_field_counts": dict(sorted(differing_fields.items())),
        "replacement_rows": replacement_rows,
        "candidate_replacement_logical_sha256": replacement_digest.hexdigest(),
        "coverage_intervals": len(coverage),
        "coverage": [_interval_dict(item) for item in coverage],
        "residual_unavailable_intervals": len(residual),
        "residual_unavailable": [_interval_dict(item) for item in residual],
        "by_symbol_era": [
            {
                "symbol": symbol,
                "year": year,
                "work_units": values.work_units,
                "row_count": values.row_count,
                "replacement_rows": values.replacement_rows,
                "terminal_status_counts": dict(sorted(values.statuses.items())),
                "classification_counts": dict(sorted(values.classification_counts.items())),
                "differing_field_counts": dict(sorted(values.differing_field_counts.items())),
            }
            for (symbol, year), values in sorted(breakdowns.items())
        ],
        "static_promotion_operations": {
            "candidate_coverage_rows": len(coverage),
            "candidate_promotion_rows": 1,
            "candidate_replacement_rows": replacement_rows,
            "candidate_run_id": run.run_id,
            "promotion_insert_semantics": "append_if_absent_without_reordering",
            "active_view_rule": "highest_existing_promotion_id",
            "tables_written": [
                "market_data.candle_reconciliation_coverage",
                "market_data.candle_reconciliation_promotions",
            ],
            "view_affected": "market_data.candles_reconciled",
            "immutable_dump_modified": False,
            "requires_database_preflight_for_exact_effects": True,
        },
    }
    return ReconciliationPromotionPreflight(payload, coverage, residual)


def _append_interval(
    available: list[VerifiedCoverageInterval],
    unavailable: list[VerifiedCoverageInterval],
    *,
    state: str,
    symbol: str,
    timeframe: str,
    start_ms: int,
    end_ms: int,
    max_intervals: int,
) -> None:
    if len(available) + len(unavailable) >= max_intervals:
        raise ValueError("promotion preflight interval output exceeds the configured safe bound")
    interval = VerifiedCoverageInterval(symbol, timeframe, start_ms, end_ms)
    (unavailable if state == "unavailable" else available).append(interval)


def _merge_intervals(
    intervals: list[VerifiedCoverageInterval],
) -> tuple[VerifiedCoverageInterval, ...]:
    merged: list[VerifiedCoverageInterval] = []
    for item in sorted(
        intervals, key=lambda value: (value.symbol, value.timeframe, value.start_ms)
    ):
        if (
            merged
            and (merged[-1].symbol, merged[-1].timeframe) == (item.symbol, item.timeframe)
            and merged[-1].end_ms == item.start_ms
        ):
            previous = merged[-1]
            merged[-1] = VerifiedCoverageInterval(
                previous.symbol,
                previous.timeframe,
                previous.start_ms,
                item.end_ms,
            )
        else:
            merged.append(item)
    return tuple(merged)


def _interval_dict(item: VerifiedCoverageInterval) -> dict[str, object]:
    return {
        "symbol": item.symbol,
        "timeframe": item.timeframe,
        "start": _iso_ms(item.start_ms),
        "end": _iso_ms(item.end_ms),
    }


def _iso_ms(value: int) -> str:
    return datetime.fromtimestamp(value / 1_000, tz=UTC).isoformat().replace("+00:00", "Z")


def _set_sha256(values: set[str]) -> str:
    return hashlib.sha256(("\n".join(sorted(values)) + "\n").encode()).hexdigest()


__all__ = [
    "MAX_PREFLIGHT_INTERVALS",
    "MAX_PREFLIGHT_WORK_UNITS",
    "ReconciliationPromotionPreflight",
    "build_reconciliation_promotion_preflight",
]
