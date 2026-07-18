"""Immutable filesystem receipts for applied reconciliation promotions."""

from __future__ import annotations

import hashlib
import json
import os
import re
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from market_structure_lab.data.reconciliation.manifests import ReconciliationRunManifest
from market_structure_lab.data.reconciliation.repository import (
    ReconciliationPromotion,
    VerifiedCoverageInterval,
    validate_reconciliation_coverage,
)

PROMOTION_RECEIPT_SCHEMA_VERSION = 1
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_RUN_ID = re.compile(r"^RR-\d{6}$")


@dataclass(frozen=True, slots=True)
class ReconciliationPromotionReceipt:
    """Checksum-bound promotion identity plus the exact admitted coverage."""

    run_id: str
    manifest_sha256: str
    replacement_logical_sha256: str
    canonical_logical_sha256: str
    promoted_at: str
    coverage: tuple[VerifiedCoverageInterval, ...]
    coverage_logical_sha256: str
    content_sha256: str
    path: Path

    @property
    def coverage_interval_count(self) -> int:
        return len(self.coverage)

    def to_dict(self) -> dict[str, Any]:
        return {
            **_receipt_payload(
                run_id=self.run_id,
                manifest_sha256=self.manifest_sha256,
                replacement_logical_sha256=self.replacement_logical_sha256,
                canonical_logical_sha256=self.canonical_logical_sha256,
                promoted_at=self.promoted_at,
                coverage=self.coverage,
                coverage_logical_sha256=self.coverage_logical_sha256,
            ),
            "content_sha256": self.content_sha256,
        }


def promotion_receipt_path(output_root: Path, run_id: str) -> Path:
    """Return the single immutable receipt path for one reconciliation run."""
    if not _RUN_ID.fullmatch(run_id):
        raise ValueError("reconciliation run ID is invalid")
    return output_root / "promotions" / f"run_id={run_id}" / "receipt.json"


def write_reconciliation_promotion_receipt(
    output_root: Path,
    run: ReconciliationRunManifest,
    promotion: ReconciliationPromotion,
    coverage: tuple[VerifiedCoverageInterval, ...],
) -> ReconciliationPromotionReceipt:
    """Publish once, allowing only byte-identical idempotent replay."""
    if promotion.run_id != run.run_id or promotion.manifest_sha256 != run.manifest_sha256:
        raise ValueError("promotion identity does not match the frozen run")
    _require_sha256("replacement logical SHA-256", promotion.replacement_logical_sha256)
    _require_sha256("canonical logical SHA-256", promotion.canonical_logical_sha256)
    _parse_canonical_utc(promotion.promoted_at)
    intervals = validate_reconciliation_coverage(run, coverage)
    coverage_hash = _coverage_logical_sha256(intervals)
    payload = _receipt_payload(
        run_id=run.run_id,
        manifest_sha256=run.manifest_sha256,
        replacement_logical_sha256=promotion.replacement_logical_sha256,
        canonical_logical_sha256=promotion.canonical_logical_sha256,
        promoted_at=promotion.promoted_at,
        coverage=intervals,
        coverage_logical_sha256=coverage_hash,
    )
    content_hash = _logical_sha256(payload)
    path = promotion_receipt_path(output_root, run.run_id)
    receipt = ReconciliationPromotionReceipt(
        run_id=run.run_id,
        manifest_sha256=run.manifest_sha256,
        replacement_logical_sha256=promotion.replacement_logical_sha256,
        canonical_logical_sha256=promotion.canonical_logical_sha256,
        promoted_at=promotion.promoted_at,
        coverage=intervals,
        coverage_logical_sha256=coverage_hash,
        content_sha256=content_hash,
        path=path,
    )
    body = json.dumps(receipt.to_dict(), indent=2, sort_keys=True).encode() + b"\n"
    if path.exists():
        existing = read_reconciliation_promotion_receipt(path)
        if existing != receipt or path.read_bytes() != body:
            raise FileExistsError("promotion receipt exists with different content")
        return existing

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.parent / f".{path.name}.{uuid.uuid4().hex}.tmp"
    try:
        with temporary.open("xb") as handle:
            handle.write(body)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(temporary, path)
        except FileExistsError:
            existing = read_reconciliation_promotion_receipt(path)
            if existing != receipt or path.read_bytes() != body:
                raise FileExistsError("promotion receipt exists with different content") from None
            return existing
    finally:
        temporary.unlink(missing_ok=True)
    return receipt


def read_reconciliation_promotion_receipt(path: Path) -> ReconciliationPromotionReceipt:
    """Read and fully verify one immutable promotion receipt."""
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or set(raw) != {
        "schema_version",
        "run_id",
        "manifest_sha256",
        "replacement_logical_sha256",
        "canonical_logical_sha256",
        "promoted_at",
        "coverage_interval_count",
        "coverage_logical_sha256",
        "coverage",
        "content_sha256",
    }:
        raise ValueError("promotion receipt content is invalid")
    if raw["schema_version"] != PROMOTION_RECEIPT_SCHEMA_VERSION:
        raise ValueError("promotion receipt schema version is invalid")
    run_id = _required_string(raw, "run_id")
    if not _RUN_ID.fullmatch(run_id):
        raise ValueError("promotion receipt run ID is invalid")
    manifest_sha256 = _required_sha256(raw, "manifest_sha256")
    replacement_sha256 = _required_sha256(raw, "replacement_logical_sha256")
    canonical_sha256 = _required_sha256(raw, "canonical_logical_sha256")
    promoted_at = _required_string(raw, "promoted_at")
    _parse_canonical_utc(promoted_at)
    coverage_raw = raw["coverage"]
    if not isinstance(coverage_raw, list):
        raise ValueError("promotion receipt coverage is invalid")
    coverage = tuple(_coverage_interval(item) for item in coverage_raw)
    count = raw["coverage_interval_count"]
    if not isinstance(count, int) or isinstance(count, bool) or count != len(coverage):
        raise ValueError("promotion receipt coverage count is invalid")
    coverage_sha256 = _required_sha256(raw, "coverage_logical_sha256")
    if coverage_sha256 != _coverage_logical_sha256(coverage):
        raise ValueError("promotion receipt coverage checksum is invalid")
    content_sha256 = _required_sha256(raw, "content_sha256")
    payload = dict(raw)
    payload.pop("content_sha256")
    if content_sha256 != _logical_sha256(payload):
        raise ValueError("promotion receipt content checksum is invalid")
    return ReconciliationPromotionReceipt(
        run_id=run_id,
        manifest_sha256=manifest_sha256,
        replacement_logical_sha256=replacement_sha256,
        canonical_logical_sha256=canonical_sha256,
        promoted_at=promoted_at,
        coverage=coverage,
        coverage_logical_sha256=coverage_sha256,
        content_sha256=content_sha256,
        path=path,
    )


def _receipt_payload(
    *,
    run_id: str,
    manifest_sha256: str,
    replacement_logical_sha256: str,
    canonical_logical_sha256: str,
    promoted_at: str,
    coverage: tuple[VerifiedCoverageInterval, ...],
    coverage_logical_sha256: str,
) -> dict[str, Any]:
    return {
        "schema_version": PROMOTION_RECEIPT_SCHEMA_VERSION,
        "run_id": run_id,
        "manifest_sha256": manifest_sha256,
        "replacement_logical_sha256": replacement_logical_sha256,
        "canonical_logical_sha256": canonical_logical_sha256,
        "promoted_at": promoted_at,
        "coverage_interval_count": len(coverage),
        "coverage_logical_sha256": coverage_logical_sha256,
        "coverage": [_coverage_dict(item) for item in coverage],
    }


def _coverage_logical_sha256(coverage: tuple[VerifiedCoverageInterval, ...]) -> str:
    return _logical_sha256([_coverage_dict(item) for item in coverage])


def _coverage_dict(item: VerifiedCoverageInterval) -> dict[str, object]:
    return {
        "symbol": item.symbol,
        "timeframe": item.timeframe,
        "start_ms": item.start_ms,
        "end_ms": item.end_ms,
    }


def _coverage_interval(raw: object) -> VerifiedCoverageInterval:
    if not isinstance(raw, dict) or set(raw) != {"symbol", "timeframe", "start_ms", "end_ms"}:
        raise ValueError("promotion receipt coverage interval is invalid")
    symbol = raw["symbol"]
    timeframe = raw["timeframe"]
    start_ms = raw["start_ms"]
    end_ms = raw["end_ms"]
    if (
        not isinstance(symbol, str)
        or not isinstance(timeframe, str)
        or not isinstance(start_ms, int)
        or isinstance(start_ms, bool)
        or not isinstance(end_ms, int)
        or isinstance(end_ms, bool)
    ):
        raise ValueError("promotion receipt coverage interval is invalid")
    return VerifiedCoverageInterval(symbol, timeframe, start_ms, end_ms)


def _logical_sha256(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _required_string(raw: dict[str, object], key: str) -> str:
    value = raw[key]
    if not isinstance(value, str) or not value:
        raise ValueError(f"promotion receipt {key} is invalid")
    return value


def _required_sha256(raw: dict[str, object], key: str) -> str:
    value = _required_string(raw, key)
    _require_sha256(key, value)
    return value


def _require_sha256(name: str, value: str) -> None:
    if not _SHA256.fullmatch(value):
        raise ValueError(f"{name} is invalid")


def _parse_canonical_utc(value: str) -> datetime:
    if not value.endswith("Z"):
        raise ValueError("promotion timestamp must be canonical UTC")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("promotion timestamp must be timezone-aware")
    return parsed.astimezone(UTC)


__all__ = [
    "PROMOTION_RECEIPT_SCHEMA_VERSION",
    "ReconciliationPromotionReceipt",
    "promotion_receipt_path",
    "read_reconciliation_promotion_receipt",
    "write_reconciliation_promotion_receipt",
]
