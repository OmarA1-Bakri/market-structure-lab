from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import UTC, datetime
from pathlib import Path

import pytest

from market_structure_lab.data.validation_source_v2 import (
    ScopedSourceStatusV2,
    discover_scoped_source_v2,
    reject_pg_restore_row_source_v2,
    verify_dump_toc_metadata_v2,
    verify_scoped_source_availability_v2,
)
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


def test_dump_metadata_accepts_only_public_candles_and_never_row_restore() -> None:
    toc = b"42; 0 0 TABLE DATA public candles owner\n"
    metadata = verify_dump_toc_metadata_v2(toc)
    assert metadata.table == "public.candles"
    with pytest.raises(ValueError, match="public.candles"):
        verify_dump_toc_metadata_v2(b"42; 0 0 TABLE DATA market_data candles owner\n")
    with pytest.raises(PermissionError, match="metadata only"):
        reject_pg_restore_row_source_v2(("pg_restore", "--data-only", "callscore.dump"))


def test_discovery_truthfully_seals_unavailable_without_reading_rows(
    issued_v2_publications, tmp_path: Path
) -> None:
    _, _, boundary = issued_v2_publications
    candidates = tmp_path / "candidates"
    candidates.mkdir()
    (candidates / "whole-table.json").write_text(
        '{"schema_version":"phase5-scoped-source-candidate-v2",'
        '"source_kind":"postgres-restored-whole-table","read_only":true,'
        '"predicate_enforcement":"client-side-post-filter","boundary_sha256":"'
        + boundary.boundary_sha256
        + '","source_identity":"whole-table"}\n',
        encoding="utf-8",
    )
    result = discover_scoped_source_v2(
        boundary=boundary,
        candidate_root=candidates,
        audit_ledger_root=tmp_path / "audit",
        output_root=tmp_path / "publication",
    )
    assert result.status is ScopedSourceStatusV2.UNAVAILABLE
    assert result.rows_read == result.final_rows == result.final_access_records == 0
    assert result.candidates[0].rejection_reason == "whole_table_source_forbidden"
    assert verify_scoped_source_availability_v2(
        result, boundary=boundary, publication_root=tmp_path / "publication"
    ) == result
    with pytest.raises(FrozenInstanceError):
        result.rows_read = 1  # type: ignore[misc]


def test_availability_capability_cannot_be_directly_constructed(
    issued_v2_publications, tmp_path: Path
) -> None:
    from market_structure_lab.data.validation_source_v2 import ScopedSourceAvailabilityV2

    _, _, boundary = issued_v2_publications
    with pytest.raises(TypeError, match="factory"):
        ScopedSourceAvailabilityV2.unsealed_for_test(boundary.boundary_sha256)
