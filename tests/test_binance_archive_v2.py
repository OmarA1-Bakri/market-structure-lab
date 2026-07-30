from __future__ import annotations

from dataclasses import replace
from dataclasses import fields
from datetime import UTC, datetime
import copy
import hashlib
import json
from pathlib import Path
import zipfile

import pytest

from market_structure_lab.data.binance_archive_v2 import (
    ArchiveBudgetsV2,
    ArchiveBudgetExceeded,
    ArchiveNetworkUnavailable,
    acquire_binance_archives_v2,
    freeze_binance_archive_requests_v2,
    load_binance_archive_acquisition_v2,
    load_binance_archive_request_manifest_for_acquisition_v2,
    parse_observed_trade_row_v2,
    verify_binance_archive_acquisition_v2,
)
from market_structure_lab.core.identity import hash_json
from market_structure_lab.research.validation_v2_models import publication_json_bytes
from market_structure_lab.data.validation_source_v2 import discover_scoped_source_v2
from market_structure_lab.research.validation_v2_models import (
    RawDumpIdentityV2,
    ReconciliationAuthorityV2,
    SourceCoverageEntryV2,
    SourceCoveragePublicationV2,
)
from market_structure_lab.research.validation_v2_splits import (
    SplitPolicyV2,
    freeze_development_split_v2,
    issue_development_read_boundary_v2,
)


@pytest.fixture
def issued_v2_publications():
    coverage = SourceCoveragePublicationV2.freeze(
        raw_dump=RawDumpIdentityV2("a" * 64, 1, "b" * 64, "public.candles:42", "v1"),
        reconciliation=ReconciliationAuthorityV2(
            "c" * 64, ("d" * 64,), ("e" * 64,), "rr-000008-promoted-only"
        ),
        compatibility_metadata_sha256="f" * 64,
        entries=tuple(
            SourceCoverageEntryV2(
                symbol,
                datetime(2024, 1, 1, tzinfo=UTC),
                datetime(2025, 1, 1, tzinfo=UTC),
                ("1m", "1h", "4h"),
                False,
                True,
                str(index) * 64,
            )
            for index, symbol in enumerate(
                ("ADAUSDT", "BNBUSDT", "DOGEUSDT", "SOLUSDT", "XRPUSDT"), 1
            )
        ),
    )
    split = freeze_development_split_v2(
        coverage=coverage,
        policy=SplitPolicyV2(6, 180, 1, 5, "public-salt", 24, 24, ("1m", "1h", "4h")),
    )
    return coverage, split, issue_development_read_boundary_v2(coverage, split)


@pytest.fixture
def unavailable_source_v2(issued_v2_publications, tmp_path: Path):
    _, _, boundary = issued_v2_publications
    candidates = tmp_path / "empty-candidates"
    candidates.mkdir()
    return discover_scoped_source_v2(
        boundary=boundary,
        candidate_root=candidates,
        audit_ledger_root=tmp_path / "source-audit",
        output_root=tmp_path / "source-publication",
    )


def test_request_freeze_rejects_final_scope_before_url_construction(
    issued_v2_publications, unavailable_source_v2, tmp_path: Path, monkeypatch
) -> None:
    from market_structure_lab.data import binance_archive_v2 as module

    _, _, boundary = issued_v2_publications
    bad = object.__new__(type(boundary))
    for name, value in boundary.constructor_fields().items():
        object.__setattr__(
            bad,
            name,
            ((boundary.forbidden_asset_symbols[0],) if name == "allowed_symbols" else value),
        )
    constructed = False

    original = module._make_request  # noqa: SLF001

    def observer(*args, **kwargs):
        nonlocal constructed
        constructed = True
        return original(*args, **kwargs)

    monkeypatch.setattr(module, "_make_request", observer)

    with pytest.raises(ValueError, match="original publication"):
        freeze_binance_archive_requests_v2(
            boundary=bad,
            source_availability=unavailable_source_v2,
            budgets=ArchiveBudgetsV2.testing(),
            output=tmp_path / "requests.json",
        )
    assert constructed is False


def test_observed_trade_tape_has_narrow_authority() -> None:
    trade = parse_observed_trade_row_v2(
        ("1", "42000.5", "0.25", "10500.125", "1704067200000"),
        archive_kind="trades",
    )
    assert trade.price == "42000.5"
    assert trade.quantity == "0.25"
    assert trade.turnover == "10500.125"
    assert not hasattr(trade, "fee")
    assert {"fees", "spread", "latency", "fill_probability", "capacity"} <= set(
        trade.unsupported_authorities
    )
    assert "unsupported_authorities" not in {item.name for item in fields(trade)}


def test_budget_schema_rejects_unbounded_or_incoherent_values() -> None:
    with pytest.raises(ValueError, match="positive"):
        replace(ArchiveBudgetsV2.testing(), max_requests=0)
    with pytest.raises(ValueError, match="chunk"):
        replace(
            ArchiveBudgetsV2.testing(),
            chunk_bytes=2_000_000,
            max_compressed_object_bytes=1_000_000,
        )


@pytest.mark.parametrize("forgery", ("forbidden_asset", "final_period", "path_date"))
def test_loaded_manifest_rejects_coherent_scope_and_path_forgery_before_io(
    forgery: str,
    issued_v2_publications,
    unavailable_source_v2,
    tmp_path: Path,
) -> None:
    from market_structure_lab.data import binance_archive_v2 as module

    _, _, boundary = issued_v2_publications
    original = freeze_binance_archive_requests_v2(
        boundary=boundary,
        source_availability=unavailable_source_v2,
        budgets=ArchiveBudgetsV2.testing(),
        output=tmp_path / "original.json",
    )
    payload = copy.deepcopy(original.to_dict())
    request = payload["requests"][0]
    assert isinstance(request, dict)
    if forgery == "forbidden_asset":
        request["symbol"] = boundary.forbidden_asset_symbols[0]
        stamp = str(request["start"])[:10]
        filename = f"{request['symbol']}-aggTrades-{stamp}.zip"
        request["object_path"] = f"/data/spot/daily/aggTrades/{request['symbol']}/{filename}"
        request["checksum_path"] = f"{request['object_path']}.CHECKSUM"
    elif forgery == "final_period":
        interval = boundary.forbidden_temporal_intervals[0]
        request["start"] = interval.start.isoformat(timespec="seconds").replace("+00:00", "Z")
        request["end"] = (
            (interval.start.replace(hour=0) + module.timedelta(days=1))
            .isoformat(timespec="seconds")
            .replace("+00:00", "Z")
        )
        stamp = str(request["start"])[:10]
        filename = f"{request['symbol']}-aggTrades-{stamp}.zip"
        request["period"] = "daily"
        request["object_path"] = f"/data/spot/daily/aggTrades/{request['symbol']}/{filename}"
        request["checksum_path"] = f"{request['object_path']}.CHECKSUM"
    else:
        request["object_path"] = str(request["object_path"]).replace(
            str(request["start"])[:7], "1999-01"
        )
        request["checksum_path"] = f"{request['object_path']}.CHECKSUM"
    request_payload = {key: value for key, value in request.items() if key != "request_sha256"}
    request["request_sha256"] = hash_json(module._REQUEST_DOMAIN, request_payload)  # noqa: SLF001
    manifest_payload = {
        key: value
        for key, value in payload.items()
        if key not in {"schema_version", "manifest_sha256"}
    }
    payload["manifest_sha256"] = hash_json(module._MANIFEST_DOMAIN, manifest_payload)  # noqa: SLF001
    forged = tmp_path / f"{forgery}.json"
    forged.write_bytes(publication_json_bytes(payload))

    with pytest.raises((PermissionError, ValueError), match="development|deterministic"):
        load_binance_archive_request_manifest_for_acquisition_v2(
            publication_path=forged,
            boundary=boundary,
        )


def test_unexpected_acquisition_error_is_not_downgraded(
    issued_v2_publications,
    unavailable_source_v2,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from market_structure_lab.data import binance_archive_v2 as module

    _, _, boundary = issued_v2_publications
    manifest_path = tmp_path / "requests.json"
    manifest = freeze_binance_archive_requests_v2(
        boundary=boundary,
        source_availability=unavailable_source_v2,
        budgets=ArchiveBudgetsV2.testing(),
        output=manifest_path,
    )
    cache = tmp_path / "cache"
    cache.mkdir()
    monkeypatch.setattr(
        module,
        "_download_small_with_retries",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("programming defect")),
    )

    with pytest.raises(AssertionError, match="programming defect"):
        acquire_binance_archives_v2(
            boundary=boundary,
            manifest=manifest,
            manifest_path=manifest_path,
            audit_ledger_root=tmp_path / "audit",
            cache_root=cache,
            output_root=tmp_path / "output",
        )
    assert not (tmp_path / "audit").exists()
    assert not (tmp_path / "output").exists()


def test_expected_failure_publishes_paired_audit_and_tamper_rejects(
    issued_v2_publications,
    unavailable_source_v2,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from market_structure_lab.data import binance_archive_v2 as module

    _, _, boundary = issued_v2_publications
    manifest_path = tmp_path / "requests.json"
    manifest = freeze_binance_archive_requests_v2(
        boundary=boundary,
        source_availability=unavailable_source_v2,
        budgets=ArchiveBudgetsV2.testing(),
        output=manifest_path,
    )
    cache = tmp_path / "cache"
    cache.mkdir()
    monkeypatch.setattr(
        module,
        "_download_small_with_retries",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            ArchiveNetworkUnavailable("fixture unavailable")
        ),
    )
    audit = tmp_path / "audit"
    output = tmp_path / "output"
    result = acquire_binance_archives_v2(
        boundary=boundary,
        manifest=manifest,
        manifest_path=manifest_path,
        audit_ledger_root=audit,
        cache_root=cache,
        output_root=output,
    )
    assert result.status == "unavailable"
    assert (audit / "publication.json").is_file()
    assert (output / "publication.json").is_file()
    assert (
        verify_binance_archive_acquisition_v2(
            result,
            boundary=boundary,
            manifest=manifest,
            manifest_path=manifest_path,
        )
        == result
    )
    assert (
        load_binance_archive_acquisition_v2(
            publication_root=output,
            cache_root=cache,
            boundary=boundary,
            manifest=manifest,
            manifest_path=manifest_path,
        )
        == result
    )
    (audit / "publication.json").write_text("{}\n", encoding="utf-8")
    with pytest.raises(ValueError, match="audit"):
        verify_binance_archive_acquisition_v2(
            result,
            boundary=boundary,
            manifest=manifest,
            manifest_path=manifest_path,
        )


@pytest.mark.parametrize(
    "attack",
    (
        "wrong_manifest",
        "partial_unavailable",
        "available_duplicate_inventory",
        "audit_request_count_mismatch",
        "unavailable_without_failure",
    ),
)
def test_loader_rejects_self_hashed_fabricated_acquisition_publications(
    attack: str,
    issued_v2_publications,
    unavailable_source_v2,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from market_structure_lab.data import binance_archive_v2 as module

    _, _, boundary = issued_v2_publications
    manifest_path = tmp_path / "requests.json"
    manifest = freeze_binance_archive_requests_v2(
        boundary=boundary,
        source_availability=unavailable_source_v2,
        budgets=ArchiveBudgetsV2.testing(),
        output=manifest_path,
    )
    cache = tmp_path / "cache"
    cache.mkdir()
    monkeypatch.setattr(
        module,
        "_download_small_with_retries",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            ArchiveNetworkUnavailable("fixture unavailable")
        ),
    )
    output = tmp_path / "output"
    acquire_binance_archives_v2(
        boundary=boundary,
        manifest=manifest,
        manifest_path=manifest_path,
        audit_ledger_root=tmp_path / "audit",
        cache_root=cache,
        output_root=output,
    )
    publication_path = output / "publication.json"
    publication = json.loads(publication_path.read_bytes())
    cached = b"fabricated archive object"
    cached_sha256 = hashlib.sha256(cached).hexdigest()
    relative = Path("sha256") / cached_sha256[:2] / cached_sha256
    cached_path = cache / relative
    cached_path.parent.mkdir(parents=True)
    cached_path.write_bytes(cached)
    fake_object = {
        "request_sha256": manifest.requests[0].request_sha256,
        "official_sha256": cached_sha256,
        "local_sha256": cached_sha256,
        "cache_object": relative.as_posix(),
        "compressed_bytes": len(cached),
        "decompressed_bytes": 0,
        "row_count": 0,
    }
    if attack == "wrong_manifest":
        publication["manifest_sha256"] = "0" * 64
    elif attack == "partial_unavailable":
        publication["objects"] = [fake_object]
        publication["compressed_bytes"] = len(cached)
    elif attack == "available_duplicate_inventory":
        publication["status"] = "available"
        publication["failure"] = None
        publication["objects"] = [copy.deepcopy(fake_object) for _ in manifest.requests]
        publication["compressed_bytes"] = len(cached) * len(manifest.requests)
    elif attack == "audit_request_count_mismatch":
        publication["request_count"] += 1
    else:
        publication["failure"] = None
    identity = {key: value for key, value in publication.items() if key != "publication_sha256"}
    publication["publication_sha256"] = hash_json(
        module._ACQUISITION_DOMAIN,
        identity,  # noqa: SLF001
    )
    publication_path.write_bytes(publication_json_bytes(publication))

    with pytest.raises(ValueError, match="acquisition|manifest|inventory|audit|failure"):
        load_binance_archive_acquisition_v2(
            publication_root=output,
            cache_root=cache,
            boundary=boundary,
            manifest=manifest,
            manifest_path=manifest_path,
        )


def test_archive_row_budget_rejects_before_reading_an_extra_row(
    issued_v2_publications,
    unavailable_source_v2,
    tmp_path: Path,
) -> None:
    from market_structure_lab.data import binance_archive_v2 as module

    _, _, boundary = issued_v2_publications
    manifest = freeze_binance_archive_requests_v2(
        boundary=boundary,
        source_availability=unavailable_source_v2,
        budgets=ArchiveBudgetsV2.testing(),
        output=tmp_path / "requests.json",
    )
    request = manifest.requests[0]
    timestamp = int(request.start.timestamp() * 1_000)
    row = f"1,42000,1,1,1,{timestamp},false,true\n"
    archive = tmp_path / "rows.zip"
    with zipfile.ZipFile(archive, "w") as output:
        output.writestr("rows.csv", row + row)

    with pytest.raises(ArchiveBudgetExceeded, match="rows"):
        module._inspect_archive(  # noqa: SLF001
            archive,
            request,
            manifest.budgets,
            remaining_decompressed=manifest.budgets.max_total_decompressed_bytes,
            remaining_rows=1,
            deadline=module.time.monotonic() + 60,
        )


def test_paired_acquisition_and_audit_publication_roll_back_together(
    issued_v2_publications,
    unavailable_source_v2,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from market_structure_lab.data import binance_archive_v2 as module

    _, _, boundary = issued_v2_publications
    manifest_path = tmp_path / "requests.json"
    manifest = freeze_binance_archive_requests_v2(
        boundary=boundary,
        source_availability=unavailable_source_v2,
        budgets=ArchiveBudgetsV2.testing(),
        output=manifest_path,
    )
    cache = tmp_path / "cache"
    cache.mkdir()
    monkeypatch.setattr(
        module,
        "_download_small_with_retries",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            ArchiveNetworkUnavailable("fixture unavailable")
        ),
    )
    audit = tmp_path / "audit"
    output = tmp_path / "output"
    original_reserve = module._reserve_publication_directory  # noqa: SLF001

    def fail_second_publish(destination: Path) -> None:
        if Path(destination) == audit:
            raise OSError("fixture second publication failure")
        original_reserve(destination)

    monkeypatch.setattr(module, "_reserve_publication_directory", fail_second_publish)
    with pytest.raises(OSError, match="second publication"):
        acquire_binance_archives_v2(
            boundary=boundary,
            manifest=manifest,
            manifest_path=manifest_path,
            audit_ledger_root=audit,
            cache_root=cache,
            output_root=output,
        )
    assert not audit.exists()
    assert not output.exists()


def test_paired_acquisition_refuses_concurrent_empty_destination_directory(
    issued_v2_publications,
    unavailable_source_v2,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from market_structure_lab.data import binance_archive_v2 as module

    _, _, boundary = issued_v2_publications
    manifest_path = tmp_path / "requests.json"
    manifest = freeze_binance_archive_requests_v2(
        boundary=boundary,
        source_availability=unavailable_source_v2,
        budgets=ArchiveBudgetsV2.testing(),
        output=manifest_path,
    )
    cache = tmp_path / "cache"
    cache.mkdir()
    monkeypatch.setattr(
        module,
        "_download_small_with_retries",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            ArchiveNetworkUnavailable("fixture unavailable")
        ),
    )
    audit = tmp_path / "audit"
    output = tmp_path / "output"
    original_reserve = module._reserve_publication_directory  # noqa: SLF001

    def race(destination: Path) -> None:
        if Path(destination) == output:
            output.mkdir()
        original_reserve(destination)

    monkeypatch.setattr(module, "_reserve_publication_directory", race)
    with pytest.raises(FileExistsError):
        acquire_binance_archives_v2(
            boundary=boundary,
            manifest=manifest,
            manifest_path=manifest_path,
            audit_ledger_root=audit,
            cache_root=cache,
            output_root=output,
        )
    assert output.is_dir()
    assert not tuple(output.iterdir())
    assert not audit.exists()
