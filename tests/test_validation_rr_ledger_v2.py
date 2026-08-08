from __future__ import annotations

from datetime import UTC, datetime, timedelta
import hashlib
import json
from pathlib import Path
from typing import Callable

import polars as pl
import pytest

from market_structure_lab.data.reconciliation import (
    ReconciliationClass,
    ReconciliationRecord,
    ReconciliationWorkUnit,
    SourceArtifactIdentity,
    TradingEnvelope,
    freeze_reconciliation_run,
    publish_work_unit,
    write_reconciliation_run,
)
from market_structure_lab.data.reconciliation.publication import read_work_unit_manifest
from market_structure_lab.data.reconciliation.manifests import read_reconciliation_run
from market_structure_lab.data.validation_rr_ledger_v2 import (
    preflight_scoped_rr_ledger_parts_v2,
    reopen_rr_ledger_inventory_v2,
    verify_rr_ledger_inventory_v2,
)


def _rr_ledger(
    tmp_path: Path, *, unit_minutes: int = 1, include_forbidden_month: bool = False
) -> tuple[Path, Path, datetime, bytes]:
    start = datetime(2024, 1, 1, tzinfo=UTC)
    starts = (
        (start, datetime(2024, 2, 1, tzinfo=UTC))
        if include_forbidden_month
        else (start,)
    )
    units = tuple(
        ReconciliationWorkUnit.create(
            "BTCUSDT",
            "1m",
            int(unit_start.timestamp() * 1000),
            int(unit_start.timestamp() * 1000) + unit_minutes * 60_000,
        )
        for unit_start in starts
    )
    run = freeze_reconciliation_run(
        run_id="RR-000008",
        cutoff=datetime(2026, 7, 16, tzinfo=UTC),
        dump_sha256="a" * 64,
        source_row_count=unit_minutes * len(units),
        mapping_version="mapping-v1",
        candidate_venue="binance",
        market_type="spot",
        source_revision="binance-public-data-v1",
        algorithm_version="row-reconciliation-v1",
        code_commit="f" * 40,
        uv_lock_sha256="b" * 64,
        envelopes=tuple(
            TradingEnvelope("BTCUSDT", "1m", unit.start_ms, unit.end_ms) for unit in units
        ),
        work_units=units,
    )
    for work_unit in units:
        publish_work_unit(
            tuple(
                ReconciliationRecord(
                    "BTCUSDT",
                    "1m",
                    work_unit.start_ms + offset,
                    ReconciliationClass.SOURCE_UNAVAILABLE,
                    None,
                    None,
                    (),
                )
                for offset in range(0, unit_minutes * 60_000, 60_000)
            ),
            output_root=tmp_path,
            run=run,
            work_unit=work_unit,
            source_artifacts=(
                SourceArtifactIdentity(
                    location="https://data.binance.vision/fixture.zip",
                    payload_sha256="c" * 64,
                    published_sha256="c" * 64,
                    source_revision="binance-public-data-v1",
                    retrieved_at="2026-07-16T00:00:00Z",
                ),
            ),
        )
    write_reconciliation_run(run, tmp_path / "RR-000008.run.json")
    root = tmp_path / "run_id=RR-000008"
    unit_directory = root / "symbol=BTCUSDT" / "year=2024" / "month=01"
    manifest = read_work_unit_manifest(unit_directory / "manifest.json")
    return root, unit_directory / "_SUCCESS", start, manifest.manifest_sha256.encode("ascii")


def _authority(root: Path) -> tuple[tuple[str, ...], tuple[str, ...]]:
    run = read_reconciliation_run(root.parent / "RR-000008.run.json")
    manifests: dict[str, tuple[str, tuple[str, ...]]] = {}
    for unit in run.work_units:
        moment = datetime.fromtimestamp(unit.start_ms / 1000, tz=UTC)
        path = (
            root
            / f"symbol={unit.symbol}"
            / f"year={moment.year:04d}"
            / f"month={moment.month:02d}"
            / "manifest.json"
        )
        manifest = read_work_unit_manifest(path)
        manifests[unit.work_unit_id] = (
            hashlib.sha256(path.read_bytes()).hexdigest(),
            tuple(part.sha256 for part in manifest.parts),
        )
    return (
        tuple(manifests[key][0] for key in sorted(manifests)),
        tuple(part for key in sorted(manifests) for part in manifests[key][1]),
    )


def _verify(
    root: Path,
    start: datetime,
    *,
    authority: tuple[tuple[str, ...], tuple[str, ...]] | None = None,
) -> None:
    manifests, parts = _authority(root) if authority is None else authority
    verify_rr_ledger_inventory_v2(
        root,
        symbols=("BTCUSDT",),
        intervals=((start, start + timedelta(minutes=1)),),
        expected_work_unit_manifest_sha256=manifests,
        expected_comparison_part_sha256=parts,
    )


def _unit_directory(root: Path) -> Path:
    return root / "symbol=BTCUSDT" / "year=2024" / "month=01"


def _rewrite_manifest(unit_directory: Path, updates: dict[str, object]) -> None:
    manifest_path = unit_directory / "manifest.json"
    raw = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert isinstance(raw, dict)
    raw.update(updates)
    raw.pop("manifest_sha256", None)
    digest = hashlib.sha256(
        json.dumps(raw, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
    ).hexdigest()
    raw["manifest_sha256"] = digest
    manifest_path.write_text(
        json.dumps(raw, indent=2, sort_keys=True, separators=(",", ": ")) + "\n",
        encoding="utf-8",
    )
    (unit_directory / "_SUCCESS").write_text(digest + "\n", encoding="utf-8")


def test_rr_inventory_rejects_partial_work_unit_before_part_hash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from market_structure_lab.data import validation_rr_ledger_v2 as module

    root, _marker_path, start, _digest = _rr_ledger(tmp_path, unit_minutes=3)

    def forbidden_hash(_path: Path) -> str:
        raise AssertionError("partial work unit part opened before scope rejection")

    monkeypatch.setattr(module, "sha256_regular", forbidden_hash)
    with pytest.raises(ValueError, match="does not contain whole frozen work units"):
        _verify(root, start)


def test_rr_inventory_opens_only_wholly_authorized_unit_parts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from market_structure_lab.data import validation_rr_ledger_v2 as module

    root, _marker_path, start, _digest = _rr_ledger(
        tmp_path, include_forbidden_month=True
    )
    opened: list[Path] = []
    original_hash = module.sha256_regular
    original_scan = module.pl.scan_parquet

    def record_hash(path: Path) -> str:
        opened.append(Path(path))
        return original_hash(path)

    def record_scan(path: Path, *args: object, **kwargs: object):
        opened.append(Path(path))
        return original_scan(path, *args, **kwargs)

    monkeypatch.setattr(module, "sha256_regular", record_hash)
    monkeypatch.setattr(module.pl, "scan_parquet", record_scan)

    _verify(root, start)

    assert opened
    assert all("month=01" in path.as_posix() for path in opened)


def test_rr_inventory_rejects_physical_part_row_count_mismatch(tmp_path: Path) -> None:
    root, _marker_path, start, _digest = _rr_ledger(tmp_path, unit_minutes=3)
    unit_directory = _unit_directory(root)
    part_path = unit_directory / "part-00000.parquet"
    frame = pl.read_parquet(part_path)
    frame.head(2).write_parquet(part_path)
    raw = json.loads((unit_directory / "manifest.json").read_text(encoding="utf-8"))
    assert isinstance(raw, dict)
    parts = raw["parts"]
    assert isinstance(parts, list) and isinstance(parts[0], dict)
    parts[0]["sha256"] = hashlib.sha256(part_path.read_bytes()).hexdigest()
    _rewrite_manifest(unit_directory, {"parts": parts})

    with pytest.raises(ValueError, match="physical row count"):
        manifests, parts = _authority(root)
        verify_rr_ledger_inventory_v2(
            root,
            symbols=("BTCUSDT",),
            intervals=((start, start + timedelta(minutes=3)),),
            expected_work_unit_manifest_sha256=manifests,
            expected_comparison_part_sha256=parts,
        )


def test_rr_inventory_rejects_self_consistent_shortened_work_unit(tmp_path: Path) -> None:
    root, _marker_path, start, _digest = _rr_ledger(tmp_path, unit_minutes=3)
    unit_directory = _unit_directory(root)
    part_path = unit_directory / "part-00000.parquet"
    frame = pl.read_parquet(part_path).head(2)
    frame.write_parquet(part_path)
    raw = json.loads((unit_directory / "manifest.json").read_text(encoding="utf-8"))
    assert isinstance(raw, dict)
    parts = raw["parts"]
    assert isinstance(parts, list) and isinstance(parts[0], dict)
    parts[0]["sha256"] = hashlib.sha256(part_path.read_bytes()).hexdigest()
    parts[0]["row_count"] = 2
    classifications = raw["classification_counts"]
    assert isinstance(classifications, dict)
    classifications["source_unavailable"] = 2
    _rewrite_manifest(
        unit_directory,
        {
            "row_count": 2,
            "classification_counts": classifications,
            "max_buffered_rows": 2,
            "parts": parts,
        },
    )

    with pytest.raises(ValueError, match="frozen minute count"):
        manifests, parts = _authority(root)
        verify_rr_ledger_inventory_v2(
            root,
            symbols=("BTCUSDT",),
            intervals=((start, start + timedelta(minutes=3)),),
            expected_work_unit_manifest_sha256=manifests,
            expected_comparison_part_sha256=parts,
        )


def test_rr_inventory_rejects_rehashed_duplicate_minute_key(tmp_path: Path) -> None:
    root, _marker_path, start, _digest = _rr_ledger(tmp_path, unit_minutes=3)
    unit_directory = _unit_directory(root)
    part_path = unit_directory / "part-00000.parquet"
    frame = pl.read_parquet(part_path)
    frame = frame.with_columns(
        pl.when(pl.int_range(pl.len()) == 1)
        .then(pl.col("open_time_ms").first())
        .otherwise(pl.col("open_time_ms"))
        .alias("open_time_ms")
    )
    frame.write_parquet(part_path)
    raw = json.loads((unit_directory / "manifest.json").read_text(encoding="utf-8"))
    assert isinstance(raw, dict)
    parts = raw["parts"]
    assert isinstance(parts, list) and isinstance(parts[0], dict)
    parts[0]["sha256"] = hashlib.sha256(part_path.read_bytes()).hexdigest()
    _rewrite_manifest(unit_directory, {"parts": parts})

    with pytest.raises(ValueError, match="missing, duplicated, or reordered"):
        manifests, parts = _authority(root)
        verify_rr_ledger_inventory_v2(
            root,
            symbols=("BTCUSDT",),
            intervals=((start, start + timedelta(minutes=3)),),
            expected_work_unit_manifest_sha256=manifests,
            expected_comparison_part_sha256=parts,
        )


def test_rr_inventory_rejects_coherent_rehash_against_frozen_coverage_authority(
    tmp_path: Path,
) -> None:
    root, _marker_path, start, _digest = _rr_ledger(tmp_path)
    frozen_authority = _authority(root)
    unit_directory = _unit_directory(root)
    part_path = unit_directory / "part-00000.parquet"
    pl.read_parquet(part_path).with_columns(
        pl.lit("exact_match").alias("classification"),
        pl.lit("a" * 64).alias("dump_row_sha256"),
    ).write_parquet(part_path)
    raw = json.loads((unit_directory / "manifest.json").read_text(encoding="utf-8"))
    assert isinstance(raw, dict)
    parts = raw["parts"]
    assert isinstance(parts, list) and isinstance(parts[0], dict)
    parts[0]["sha256"] = hashlib.sha256(part_path.read_bytes()).hexdigest()
    _rewrite_manifest(
        unit_directory,
        {
            "classification_counts": {"exact_match": 1},
            "parts": parts,
        },
    )

    with pytest.raises(ValueError, match="frozen coverage authority"):
        _verify(root, start, authority=frozen_authority)


def test_rr_inventory_rejects_stale_manifest_classification_counts(tmp_path: Path) -> None:
    root, _marker_path, start, _digest = _rr_ledger(tmp_path)
    unit_directory = _unit_directory(root)
    part_path = unit_directory / "part-00000.parquet"
    pl.read_parquet(part_path).with_columns(
        pl.lit("exact_match").alias("classification"),
        pl.lit("a" * 64).alias("dump_row_sha256"),
    ).write_parquet(part_path)
    raw = json.loads((unit_directory / "manifest.json").read_text(encoding="utf-8"))
    assert isinstance(raw, dict)
    parts = raw["parts"]
    assert isinstance(parts, list) and isinstance(parts[0], dict)
    parts[0]["sha256"] = hashlib.sha256(part_path.read_bytes()).hexdigest()
    _rewrite_manifest(unit_directory, {"parts": parts})
    forged_authority = _authority(root)

    with pytest.raises(ValueError, match="classifications differ"):
        _verify(root, start, authority=forged_authority)


@pytest.mark.parametrize("line_ending", (b"\n", b"\r\n"), ids=("lf", "crlf"))
def test_rr_success_marker_accepts_one_portable_line_ending(
    tmp_path: Path, line_ending: bytes
) -> None:
    root, marker_path, start, digest = _rr_ledger(tmp_path)
    marker_path.write_bytes(digest + line_ending)

    _verify(root, start)


def _substitute_digest(digest: bytes) -> bytes:
    replacement = b"0" if digest[:1] != b"0" else b"1"
    return replacement + digest[1:] + b"\n"


@pytest.mark.parametrize(
    "marker_bytes",
    (
        lambda digest: digest,
        lambda digest: digest + b" \n",
        lambda digest: digest + b"\n\n",
        lambda _digest: b"A" * 64 + b"\n",
        lambda _digest: b"g" * 64 + b"\n",
        _substitute_digest,
    ),
    ids=(
        "no-newline",
        "extra-whitespace",
        "multiple-lines",
        "uppercase",
        "nonhex",
        "substitution",
    ),
)
def test_rr_success_marker_rejects_noncanonical_or_unbound_content(
    tmp_path: Path, marker_bytes: Callable[[bytes], bytes]
) -> None:
    root, marker_path, start, digest = _rr_ledger(tmp_path)
    marker_path.write_bytes(marker_bytes(digest))

    with pytest.raises(ValueError, match="success marker differs"):
        _verify(root, start)


def test_rr_verified_inventory_reopens_and_preflights_with_crlf(tmp_path: Path) -> None:
    root, marker_path, start, digest = _rr_ledger(tmp_path)
    inventory = verify_rr_ledger_inventory_v2(
        root,
        symbols=("BTCUSDT",),
        intervals=((start, start + timedelta(minutes=1)),),
        expected_work_unit_manifest_sha256=_authority(root)[0],
        expected_comparison_part_sha256=_authority(root)[1],
    )
    marker_path.write_bytes(digest + b"\r\n")

    reopened = reopen_rr_ledger_inventory_v2(
        inventory.verifier_payload(),
        symbols=("BTCUSDT",),
        intervals=((start, start + timedelta(minutes=1)),),
        expected_work_unit_manifest_sha256=_authority(root)[0],
        expected_comparison_part_sha256=_authority(root)[1],
    )
    preflight_scoped_rr_ledger_parts_v2(
        reopened,
        symbol="BTCUSDT",
        start=start,
        end=start + timedelta(minutes=1),
    )


@pytest.mark.parametrize(
    "marker_bytes",
    (
        lambda digest: digest,
        lambda digest: digest + b" \n",
        lambda digest: digest + b"\n\n",
        lambda _digest: b"A" * 64 + b"\n",
        lambda _digest: b"g" * 64 + b"\n",
        _substitute_digest,
    ),
    ids=(
        "no-newline",
        "extra-whitespace",
        "multiple-lines",
        "uppercase",
        "nonhex",
        "substitution",
    ),
)
def test_rr_verified_inventory_rejects_malformed_marker_when_parts_reopen(
    tmp_path: Path, marker_bytes: Callable[[bytes], bytes]
) -> None:
    root, marker_path, start, digest = _rr_ledger(tmp_path)
    inventory = verify_rr_ledger_inventory_v2(
        root,
        symbols=("BTCUSDT",),
        intervals=((start, start + timedelta(minutes=1)),),
        expected_work_unit_manifest_sha256=_authority(root)[0],
        expected_comparison_part_sha256=_authority(root)[1],
    )
    marker_path.write_bytes(marker_bytes(digest))

    with pytest.raises(ValueError, match="success marker changed"):
        preflight_scoped_rr_ledger_parts_v2(
            inventory,
            symbol="BTCUSDT",
            start=start,
            end=start + timedelta(minutes=1),
        )
