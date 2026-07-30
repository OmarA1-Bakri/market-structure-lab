from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest

from market_structure_lab.data.binance_archive_v2 import (
    ArchiveBudgetsV2,
    freeze_binance_archive_requests_v2,
    parse_observed_trade_row_v2,
)
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
            (
                (boundary.forbidden_asset_symbols[0],)
                if name == "allowed_symbols"
                else value
            ),
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


def test_budget_schema_rejects_unbounded_or_incoherent_values() -> None:
    with pytest.raises(ValueError, match="positive"):
        replace(ArchiveBudgetsV2.testing(), max_requests=0)
    with pytest.raises(ValueError, match="chunk"):
        replace(
            ArchiveBudgetsV2.testing(),
            chunk_bytes=2_000_000,
            max_compressed_object_bytes=1_000_000,
        )
