from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from market_structure_lab.data.binance_archive_v2 import (
    ArchiveBudgetsV2,
    freeze_binance_archive_requests_v2,
    verify_binance_archive_request_manifest_v2,
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
def unavailable_source_v2(issued_v2_publications, tmp_path):
    _, _, boundary = issued_v2_publications
    candidates = tmp_path / "empty-candidates"
    candidates.mkdir()
    return discover_scoped_source_v2(
        boundary=boundary,
        candidate_root=candidates,
        audit_ledger_root=tmp_path / "source-audit",
        output_root=tmp_path / "source-publication",
    )


def test_manifest_is_offline_content_addressed_and_development_only(
    issued_v2_publications, unavailable_source_v2, tmp_path
) -> None:
    _, _, boundary = issued_v2_publications
    manifest = freeze_binance_archive_requests_v2(
        boundary=boundary,
        source_availability=unavailable_source_v2,
        budgets=ArchiveBudgetsV2.testing(max_requests=10_000),
        output=tmp_path / "requests.json",
    )
    assert manifest.boundary_sha256 == boundary.boundary_sha256
    assert manifest.allowed_origin == "https://data.binance.vision"
    assert all(request.symbol in boundary.allowed_symbols for request in manifest.requests)
    assert all(
        any(
            request.start >= interval.start and request.end <= interval.end + timedelta(days=1)
            for interval in boundary.allowed_intervals
        )
        for request in manifest.requests
    )
    assert (
        verify_binance_archive_request_manifest_v2(
            manifest,
            boundary=boundary,
            source_availability=unavailable_source_v2,
            publication_path=tmp_path / "requests.json",
        )
        == manifest
    )
