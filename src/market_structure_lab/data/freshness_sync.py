"""Append-only freshness recovery, terminal reporting, and atomic artifacts."""

from __future__ import annotations

import hashlib
import json
import os
import re
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Mapping, Sequence

from sqlalchemy import Engine, text

from market_structure_lab.data.freshness import (
    FreshnessManifest,
    FreshnessSymbolPlan,
    build_freshness_manifest,
    read_freshness_manifest,
)
from market_structure_lab.data.gaps import (
    GapRange,
    ObservedEnvelope,
    ProvenanceState,
    RecoveryManifest,
)
from market_structure_lab.data.recovery import GapResolution, run_recovery
from market_structure_lab.data.sources.base import MarketDataSource

FRESHNESS_REPORT_VERSION = 1
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class FreshnessRunStatus(StrEnum):
    """Terminal freshness state after one frozen sync attempt."""

    UP_TO_DATE = "up_to_date"
    RECOVERED = "recovered"
    PARTIALLY_RECOVERED = "partially_recovered"
    PROVIDER_ABSENT = "provider_absent"
    NON_TRADING = "non_trading"
    PROVENANCE_PENDING = "provenance_pending"
    SOURCE_CONFLICT = "source_conflict"
    SOURCE_UNAVAILABLE = "source_unavailable"
    FETCH_FAILED = "fetch_failed"
    UNRESOLVED = "unresolved"


@dataclass(frozen=True, slots=True)
class FreshnessSymbolReport:
    symbol: str
    timeframe: str
    compatibility_state: ProvenanceState
    before_missing_ranges: tuple[GapRange, ...]
    after_missing_ranges: tuple[GapRange, ...]
    before_missing_minutes: int
    after_missing_minutes: int
    recovered_minutes: int
    inserted_rows: int
    status: FreshnessRunStatus
    reason: str
    current_through_cutoff: bool

    def __post_init__(self) -> None:
        if not self.symbol or self.timeframe != "1m":
            raise ValueError("freshness report requires a 1m symbol")
        before = sum(gap.expected_minutes for gap in self.before_missing_ranges)
        after = sum(gap.expected_minutes for gap in self.after_missing_ranges)
        if before != self.before_missing_minutes or after != self.after_missing_minutes:
            raise ValueError("freshness report range totals do not match minute totals")
        if self.recovered_minutes != before - after or self.recovered_minutes < 0:
            raise ValueError("freshness report does not conserve missing minutes")
        if self.inserted_rows < 0 or not self.reason:
            raise ValueError("freshness report requires non-negative inserts and a reason")
        if self.current_through_cutoff != (after == 0):
            raise ValueError("freshness current flag does not match remaining missing minutes")

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "compatibility_state": self.compatibility_state.value,
            "before_missing_ranges": [asdict(gap) for gap in self.before_missing_ranges],
            "after_missing_ranges": [asdict(gap) for gap in self.after_missing_ranges],
            "before_missing_minutes": self.before_missing_minutes,
            "after_missing_minutes": self.after_missing_minutes,
            "recovered_minutes": self.recovered_minutes,
            "inserted_rows": self.inserted_rows,
            "status": self.status.value,
            "reason": self.reason,
            "current_through_cutoff": self.current_through_cutoff,
        }


@dataclass(frozen=True, slots=True)
class FreshnessReport:
    report_version: int
    manifest_sha256: str
    compatibility_manifest_sha256: str
    dump_sha256: str
    as_of: str
    recovery_run_id: uuid.UUID | None
    recovery_logical_hash: str | None
    before_missing_minutes: int
    after_missing_minutes: int
    recovered_minutes: int
    inserted_rows: int
    coverage_conserved: bool
    symbols: tuple[FreshnessSymbolReport, ...]

    def __post_init__(self) -> None:
        if self.report_version != FRESHNESS_REPORT_VERSION:
            raise ValueError(f"unsupported freshness report version: {self.report_version}")
        for digest in (
            self.manifest_sha256,
            self.compatibility_manifest_sha256,
            self.dump_sha256,
        ):
            if not _SHA256.fullmatch(digest):
                raise ValueError("freshness report identities must be lowercase SHA-256 digests")
        if self.recovery_logical_hash is not None and not _SHA256.fullmatch(
            self.recovery_logical_hash
        ):
            raise ValueError("recovery logical hash must be a lowercase SHA-256 digest")
        if tuple(sorted(self.symbols, key=lambda item: item.symbol)) != self.symbols:
            raise ValueError("freshness report symbols must be deterministically ordered")
        if len({item.symbol for item in self.symbols}) != len(self.symbols):
            raise ValueError("freshness report contains duplicate symbols")
        before = sum(item.before_missing_minutes for item in self.symbols)
        after = sum(item.after_missing_minutes for item in self.symbols)
        recovered = sum(item.recovered_minutes for item in self.symbols)
        inserted = sum(item.inserted_rows for item in self.symbols)
        if (before, after, recovered, inserted) != (
            self.before_missing_minutes,
            self.after_missing_minutes,
            self.recovered_minutes,
            self.inserted_rows,
        ):
            raise ValueError("freshness report totals do not match per-symbol evidence")
        if not self.coverage_conserved or after != before - recovered:
            raise ValueError("freshness report global coverage is not conserved")

    def to_dict(self) -> dict[str, Any]:
        return {
            "report_version": self.report_version,
            "manifest_sha256": self.manifest_sha256,
            "compatibility_manifest_sha256": self.compatibility_manifest_sha256,
            "dump_sha256": self.dump_sha256,
            "as_of": self.as_of,
            "recovery_run_id": (
                None if self.recovery_run_id is None else str(self.recovery_run_id)
            ),
            "recovery_logical_hash": self.recovery_logical_hash,
            "before_missing_minutes": self.before_missing_minutes,
            "after_missing_minutes": self.after_missing_minutes,
            "recovered_minutes": self.recovered_minutes,
            "inserted_rows": self.inserted_rows,
            "coverage_conserved": self.coverage_conserved,
            "symbols": [item.to_dict() for item in self.symbols],
        }

    def canonical_bytes(self) -> bytes:
        return (_canonical_json(self.to_dict()) + "\n").encode()

    def sha256(self) -> str:
        return hashlib.sha256(self.canonical_bytes()).hexdigest()


@dataclass(frozen=True, slots=True)
class FreshnessArtifactPaths:
    manifest: Path
    report: Path
    latest: Path


def build_freshness_recovery_manifest(
    freshness: FreshnessManifest,
    compatibility: RecoveryManifest,
) -> RecoveryManifest:
    """Adapt only reviewed eligible ranges to the existing recovery engine."""
    _verify_compatibility(freshness, compatibility)
    envelopes = tuple(
        ObservedEnvelope(
            item.symbol,
            item.timeframe,
            item.canonical_state.first_open_time_ms,
            item.canonical_state.last_open_time_ms,
            item.canonical_state.row_count,
        )
        for item in freshness.symbols
    )
    gaps = tuple(gap for item in freshness.symbols for gap in item.eligible_ranges)
    return RecoveryManifest(
        manifest_version=1,
        source_identity=freshness.dump_identity,
        as_of=freshness.as_of,
        candidate_venue=freshness.candidate_venue,
        market_type=freshness.market_type,
        envelopes=envelopes,
        gaps=gaps,
        provenance_validation=dict(compatibility.provenance_validation),
    )


def build_freshness_report(
    before: FreshnessManifest,
    after: FreshnessManifest,
    *,
    inserted_by_symbol: Mapping[str, int] | None = None,
    resolutions_by_symbol: Mapping[str, Sequence[GapResolution]] | None = None,
    recovery_run_id: uuid.UUID | None = None,
    recovery_logical_hash: str | None = None,
) -> FreshnessReport:
    """Conserve frozen missing minutes and classify every canonical symbol."""
    if before.as_of != after.as_of:
        raise ValueError("freshness re-plan must use the same frozen cutoff")
    if before.dump_identity != after.dump_identity:
        raise ValueError("freshness re-plan changed the immutable dump identity")
    if before.compatibility_manifest_sha256 != after.compatibility_manifest_sha256:
        raise ValueError("freshness re-plan changed the reviewed compatibility artifact")
    before_by_symbol = {item.symbol: item for item in before.symbols}
    after_by_symbol = {item.symbol: item for item in after.symbols}
    if set(before_by_symbol) != set(after_by_symbol):
        raise ValueError("freshness re-plan must cover the same canonical symbols")
    inserted = {} if inserted_by_symbol is None else dict(inserted_by_symbol)
    resolutions = {} if resolutions_by_symbol is None else resolutions_by_symbol
    if set(inserted).difference(before_by_symbol):
        raise ValueError("insert counts contain a symbol outside the frozen plan")
    if set(resolutions).difference(before_by_symbol):
        raise ValueError("recovery resolutions contain a symbol outside the frozen plan")

    symbols: list[FreshnessSymbolReport] = []
    for symbol in sorted(before_by_symbol):
        prior = before_by_symbol[symbol]
        current = after_by_symbol[symbol]
        if prior.provenance_state is not current.provenance_state:
            raise ValueError(f"freshness re-plan changed provenance for {symbol}")
        before_missing = prior.canonical_state.missing_minutes
        after_missing = current.canonical_state.missing_minutes
        if after_missing > before_missing:
            raise ValueError(f"freshness recovery increased missing minutes for {symbol}")
        terminal = _terminal_status(prior, current, tuple(resolutions.get(symbol, ())))
        symbols.append(
            FreshnessSymbolReport(
                symbol=symbol,
                timeframe=prior.timeframe,
                compatibility_state=prior.provenance_state,
                before_missing_ranges=prior.missing_ranges,
                after_missing_ranges=current.missing_ranges,
                before_missing_minutes=before_missing,
                after_missing_minutes=after_missing,
                recovered_minutes=before_missing - after_missing,
                inserted_rows=int(inserted.get(symbol, 0)),
                status=terminal,
                reason=_terminal_reason(terminal, before_missing, after_missing),
                current_through_cutoff=after_missing == 0,
            )
        )
    frozen = tuple(symbols)
    return FreshnessReport(
        report_version=FRESHNESS_REPORT_VERSION,
        manifest_sha256=before.sha256(),
        compatibility_manifest_sha256=before.compatibility_manifest_sha256,
        dump_sha256=before.dump_identity.dump_sha256.lower(),
        as_of=before.as_of,
        recovery_run_id=recovery_run_id,
        recovery_logical_hash=recovery_logical_hash,
        before_missing_minutes=sum(item.before_missing_minutes for item in frozen),
        after_missing_minutes=sum(item.after_missing_minutes for item in frozen),
        recovered_minutes=sum(item.recovered_minutes for item in frozen),
        inserted_rows=sum(item.inserted_rows for item in frozen),
        coverage_conserved=True,
        symbols=frozen,
    )


def run_freshness_sync(
    engine: Engine,
    before: FreshnessManifest,
    compatibility: RecoveryManifest,
    source: MarketDataSource,
) -> FreshnessReport:
    """Recover one frozen plan, then inspect the canonical view at the same cutoff."""
    recovery = build_freshness_recovery_manifest(before, compatibility)
    summary = run_recovery(engine, recovery, source)
    after = _replan_freshness(engine, before, compatibility)
    inserted, resolutions = _load_recovery_evidence(engine, summary.run_id)
    return build_freshness_report(
        before,
        after,
        inserted_by_symbol=inserted,
        resolutions_by_symbol=resolutions,
        recovery_run_id=summary.run_id,
        recovery_logical_hash=summary.logical_hash,
    )


def freshness_exit_code(report: FreshnessReport) -> int:
    """Return the scheduler contract: current=0, trustworthy stale=2."""
    healthy = {FreshnessRunStatus.UP_TO_DATE, FreshnessRunStatus.RECOVERED}
    return 0 if all(item.status in healthy for item in report.symbols) else 2


def write_freshness_artifacts(
    manifest: FreshnessManifest,
    report: FreshnessReport,
    directory: Path,
) -> FreshnessArtifactPaths:
    """Persist immutable checksum envelopes, then atomically advance latest.json."""
    if report.manifest_sha256 != manifest.sha256():
        raise ValueError("freshness report does not belong to the supplied manifest")
    directory.mkdir(parents=True, exist_ok=True)
    stamp = _artifact_stamp(manifest.as_of)
    prefix = f"{stamp}-{manifest.sha256()[:12]}"
    manifest_path = directory / f"{prefix}.plan.json"
    report_path = directory / f"{prefix}-{report.sha256()[:12]}.report.json"
    latest_path = directory / "latest.json"
    manifest_envelope = {
        "manifest": manifest.to_dict(),
        "sha256": manifest.sha256(),
    }
    report_envelope = {"report": report.to_dict(), "sha256": report.sha256()}
    _write_immutable_manifest(
        manifest_path,
        manifest,
        (_canonical_json(manifest_envelope) + "\n").encode(),
    )
    _write_immutable(report_path, (_canonical_json(report_envelope) + "\n").encode())
    _write_atomic(
        latest_path,
        {
            "as_of": manifest.as_of,
            "manifest": manifest_path.name,
            "manifest_sha256": manifest.sha256(),
            "report": report_path.name,
            "report_sha256": report.sha256(),
        },
    )
    return FreshnessArtifactPaths(manifest_path, report_path, latest_path)


def read_freshness_report(path: Path) -> FreshnessReport:
    envelope = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(envelope, dict):
        raise ValueError("freshness report envelope must be a JSON object")
    raw = envelope.get("report")
    expected = envelope.get("sha256")
    if not isinstance(raw, dict) or not isinstance(expected, str):
        raise ValueError("freshness report envelope is missing report or checksum")
    actual = hashlib.sha256((_canonical_json(raw) + "\n").encode()).hexdigest()
    if actual != expected:
        raise ValueError("freshness report checksum does not match its content")
    report = _report_from_dict(raw)
    if report.sha256() != expected:
        raise ValueError("freshness report checksum does not match its typed content")
    return report


def read_latest_freshness_report(directory: Path) -> FreshnessReport:
    pointer = json.loads((directory / "latest.json").read_text(encoding="utf-8"))
    if not isinstance(pointer, dict) or not isinstance(pointer.get("report"), str):
        raise ValueError("latest freshness pointer is invalid")
    filename = str(pointer["report"])
    if Path(filename).name != filename:
        raise ValueError("latest freshness report path must be a local filename")
    report = read_freshness_report(directory / filename)
    if pointer.get("report_sha256") != report.sha256():
        raise ValueError("latest freshness pointer report checksum does not match")
    return report


def _verify_compatibility(
    freshness: FreshnessManifest,
    compatibility: RecoveryManifest,
) -> None:
    if compatibility.sha256() != freshness.compatibility_manifest_sha256:
        raise ValueError("freshness plan does not match the reviewed compatibility artifact")
    if compatibility.source_identity != freshness.dump_identity:
        raise ValueError("reviewed compatibility dump identity changed")
    if (
        compatibility.candidate_venue != freshness.candidate_venue
        or compatibility.market_type != freshness.market_type
    ):
        raise ValueError("reviewed compatibility market identity changed")
    expected = {item.symbol: item.provenance_state for item in freshness.symbols}
    if dict(compatibility.provenance_validation) != expected:
        raise ValueError("reviewed compatibility states differ from the freshness plan")
    envelope_symbols = {item.symbol for item in compatibility.envelopes}
    if envelope_symbols != set(expected):
        raise ValueError("reviewed compatibility symbols differ from the freshness plan")


def _terminal_status(
    before: FreshnessSymbolPlan,
    after: FreshnessSymbolPlan,
    resolutions: tuple[GapResolution, ...],
) -> FreshnessRunStatus:
    state_status = {
        ProvenanceState.PENDING: FreshnessRunStatus.PROVENANCE_PENDING,
        ProvenanceState.SOURCE_CONFLICT: FreshnessRunStatus.SOURCE_CONFLICT,
        ProvenanceState.SOURCE_UNAVAILABLE: FreshnessRunStatus.SOURCE_UNAVAILABLE,
    }.get(before.provenance_state)
    if state_status is not None:
        return state_status
    if after.canonical_state.missing_minutes == 0:
        if before.canonical_state.missing_minutes:
            return FreshnessRunStatus.RECOVERED
        return FreshnessRunStatus.UP_TO_DATE
    priority = (
        (GapResolution.FETCH_FAILED, FreshnessRunStatus.FETCH_FAILED),
        (GapResolution.UNRESOLVED, FreshnessRunStatus.UNRESOLVED),
        (GapResolution.SOURCE_UNAVAILABLE, FreshnessRunStatus.SOURCE_UNAVAILABLE),
        (GapResolution.SOURCE_CONFLICT, FreshnessRunStatus.SOURCE_CONFLICT),
        (GapResolution.PARTIALLY_RECOVERED, FreshnessRunStatus.PARTIALLY_RECOVERED),
        (GapResolution.PROVIDER_ABSENT, FreshnessRunStatus.PROVIDER_ABSENT),
        (GapResolution.NON_TRADING, FreshnessRunStatus.NON_TRADING),
    )
    for resolution, status in priority:
        if resolution in resolutions:
            return status
    return FreshnessRunStatus.UNRESOLVED


def _terminal_reason(
    status: FreshnessRunStatus,
    before_missing: int,
    after_missing: int,
) -> str:
    if status is FreshnessRunStatus.UP_TO_DATE:
        return "canonical series was already current through the frozen cutoff"
    if status is FreshnessRunStatus.RECOVERED:
        return f"recovered all {before_missing} missing minute(s)"
    if status is FreshnessRunStatus.PROVENANCE_PENDING:
        return "provenance_pending: compatibility review is not approved"
    if status is FreshnessRunStatus.SOURCE_CONFLICT:
        return "source_conflict: reviewed source disagrees with dump observations"
    if status is FreshnessRunStatus.SOURCE_UNAVAILABLE:
        return "source_unavailable: authoritative observations could not be retrieved"
    return f"{status.value}: {after_missing} missing minute(s) remain"


def _replan_freshness(
    engine: Engine,
    before: FreshnessManifest,
    compatibility: RecoveryManifest,
) -> FreshnessManifest:
    with engine.connect() as connection:
        return build_freshness_manifest(
            connection,
            dump_identity=before.dump_identity,
            compatibility_manifest=compatibility,
            compatibility_manifest_sha256=before.compatibility_manifest_sha256,
            as_of=datetime.fromisoformat(before.as_of.replace("Z", "+00:00")),
            candidate_venue=before.candidate_venue,
            market_type=before.market_type,
        )


def _load_recovery_evidence(
    engine: Engine,
    run_id: uuid.UUID,
) -> tuple[dict[str, int], dict[str, tuple[GapResolution, ...]]]:
    with engine.connect() as connection:
        inserted = {
            str(row.symbol): int(row.inserted_rows)
            for row in connection.execute(
                text(
                    "SELECT symbol, count(*) AS inserted_rows "
                    "FROM market_data.candle_supplements "
                    "WHERE run_id=:run_id AND validation_status='validated' "
                    "GROUP BY symbol ORDER BY symbol"
                ),
                {"run_id": run_id},
            )
        }
        grouped: dict[str, list[GapResolution]] = {}
        rows = connection.execute(
            text(
                "SELECT symbol, resolution FROM market_data.candle_gap_resolutions "
                "WHERE run_id=:run_id ORDER BY symbol, gap_start, gap_id"
            ),
            {"run_id": run_id},
        )
        for row in rows:
            grouped.setdefault(str(row.symbol), []).append(GapResolution(str(row.resolution)))
    return inserted, {symbol: tuple(values) for symbol, values in grouped.items()}


def _report_from_dict(raw: Mapping[str, Any]) -> FreshnessReport:
    symbols = tuple(
        FreshnessSymbolReport(
            symbol=str(item["symbol"]),
            timeframe=str(item["timeframe"]),
            compatibility_state=ProvenanceState(item["compatibility_state"]),
            before_missing_ranges=tuple(GapRange(**gap) for gap in item["before_missing_ranges"]),
            after_missing_ranges=tuple(GapRange(**gap) for gap in item["after_missing_ranges"]),
            before_missing_minutes=int(item["before_missing_minutes"]),
            after_missing_minutes=int(item["after_missing_minutes"]),
            recovered_minutes=int(item["recovered_minutes"]),
            inserted_rows=int(item["inserted_rows"]),
            status=FreshnessRunStatus(item["status"]),
            reason=str(item["reason"]),
            current_through_cutoff=bool(item["current_through_cutoff"]),
        )
        for item in raw["symbols"]
    )
    run_id = raw.get("recovery_run_id")
    return FreshnessReport(
        report_version=int(raw["report_version"]),
        manifest_sha256=str(raw["manifest_sha256"]),
        compatibility_manifest_sha256=str(raw["compatibility_manifest_sha256"]),
        dump_sha256=str(raw["dump_sha256"]),
        as_of=str(raw["as_of"]),
        recovery_run_id=None if run_id is None else uuid.UUID(str(run_id)),
        recovery_logical_hash=(
            None if raw.get("recovery_logical_hash") is None else str(raw["recovery_logical_hash"])
        ),
        before_missing_minutes=int(raw["before_missing_minutes"]),
        after_missing_minutes=int(raw["after_missing_minutes"]),
        recovered_minutes=int(raw["recovered_minutes"]),
        inserted_rows=int(raw["inserted_rows"]),
        coverage_conserved=bool(raw["coverage_conserved"]),
        symbols=symbols,
    )


def _artifact_stamp(as_of: str) -> str:
    parsed = datetime.fromisoformat(as_of.replace("Z", "+00:00"))
    return parsed.strftime("%Y%m%dT%H%M%SZ")


def _write_immutable(path: Path, payload: bytes) -> None:
    if path.exists():
        if path.read_bytes() != payload:
            raise ValueError(
                f"immutable freshness artifact already exists with different content: {path}"
            )
        return
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_bytes(payload)
    os.replace(temporary, path)


def _write_immutable_manifest(
    path: Path,
    manifest: FreshnessManifest,
    payload: bytes,
) -> None:
    if path.exists():
        if read_freshness_manifest(path) != manifest:
            raise ValueError(
                f"immutable freshness artifact already exists with different content: {path}"
            )
        return
    _write_immutable(path, payload)


def _write_atomic(path: Path, value: Mapping[str, object]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(_canonical_json(value) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def _canonical_json(value: Mapping[str, Any]) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


__all__ = [
    "FreshnessArtifactPaths",
    "FreshnessReport",
    "FreshnessRunStatus",
    "FreshnessSymbolReport",
    "build_freshness_recovery_manifest",
    "build_freshness_report",
    "freshness_exit_code",
    "read_freshness_report",
    "read_latest_freshness_report",
    "run_freshness_sync",
    "write_freshness_artifacts",
]
