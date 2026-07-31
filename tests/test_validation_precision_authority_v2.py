from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
import hashlib
from pathlib import Path

import pytest

from market_structure_lab.core.identity import hash_json
from market_structure_lab.data.validation_precision_authority_v2 import (
    PrecisionAuthorityRequestV2,
    PrecisionAuthorityStatusV2,
    PrecisionRequirementStatusV2,
    PrecisionSourceKindV2,
    load_validation_precision_authority_v2,
    precision_requirement_status_v2,
    publish_validation_precision_authority_v2,
    verified_effective_precision_v2,
)
from market_structure_lab.research.validation_v2_models import publication_json_bytes

pytest_plugins = ("test_aggregate_publication_v2",)


def test_precision_status_contract_is_exact() -> None:
    assert tuple(item.value for item in PrecisionAuthorityStatusV2) == (
        "available",
        "incomplete",
        "unavailable",
    )
    assert tuple(item.value for item in PrecisionRequirementStatusV2) == (
        "ready",
        "not_evaluated",
    )


def _parents(v2_chain, tmp_path: Path):
    import test_aggregate_publication_v2 as aggregate_tests

    minute = aggregate_tests._publish_minute(v2_chain, tmp_path, suffix="-precision")
    aggregate = aggregate_tests._publish_aggregate(
        v2_chain, minute, tmp_path / "aggregate-precision"
    )
    coverage, split, boundary, availability = v2_chain
    return coverage, split, boundary, availability, minute, aggregate


def _request(boundary, kind: PrecisionSourceKindV2, *, limitation: str | None):
    return PrecisionAuthorityRequestV2.from_boundary(
        boundary=boundary,
        venue="binance",
        query_source="api.binance.com/api/v3/exchangeInfo",
        source_kind=kind,
        retrieval_identity_sha256="1" * 64,
        checkpoint_identity_sha256="2" * 64,
        limitation=limitation,
    )


def _historical_evidence(tmp_path: Path, boundary, monkeypatch, *, omit_last=False):
    from market_structure_lab.data import validation_precision_authority_v2 as module

    symbols = boundary.allowed_symbols[:-1] if omit_last else boundary.allowed_symbols
    entries = [
        {
            "symbol": symbol,
            "effective_start": interval.start.isoformat(timespec="seconds").replace("+00:00", "Z"),
            "effective_end": interval.end.isoformat(timespec="seconds").replace("+00:00", "Z"),
            "price_step": "0.01",
            "price_origin": "0",
        }
        for symbol in symbols
        for interval in boundary.allowed_intervals
    ]
    payload = {
        "schema_version": "phase5-publisher-historical-price-precision-schedule-v2",
        "venue": "binance",
        "query_source": "api.binance.com/api/v3/exchangeInfo",
        "published_at": "2024-12-31T00:00:00Z",
        "retrieval_identity_sha256": "1" * 64,
        "checkpoint_identity_sha256": "2" * 64,
        "entries": entries,
    }
    source = {
        **payload,
        "source_identity_sha256": hash_json(
            "phase5-publisher-historical-price-precision-schedule-v2", payload
        ),
    }
    source_bytes = publication_json_bytes(source)
    source_path = tmp_path / "historical-source.json"
    source_path.write_bytes(source_bytes)
    verifier_payload = {
        "schema_version": "phase5-publisher-historical-price-precision-verifier-v2",
        "authority_source_sha256": hashlib.sha256(source_bytes).hexdigest(),
        "source_identity_sha256": source["source_identity_sha256"],
        "venue": "binance",
        "query_source": "api.binance.com/api/v3/exchangeInfo",
        "retrieval_identity_sha256": "1" * 64,
        "checkpoint_identity_sha256": "2" * 64,
        "publisher_verified": True,
        "immutable_original": True,
    }
    verifier = {
        **verifier_payload,
        "verification_sha256": hash_json(
            "phase5-publisher-historical-price-precision-verifier-v2",
            verifier_payload,
        ),
    }
    verifier_bytes = publication_json_bytes(verifier)
    verifier_path = tmp_path / "historical-verifier.json"
    verifier_path.write_bytes(verifier_bytes)
    monkeypatch.setattr(
        module,
        "_TRUSTED_HISTORICAL_PRECISION_VERIFIER_SHA256",
        frozenset({hashlib.sha256(verifier_bytes).hexdigest()}),
    )
    return source_path, verifier_path


def test_current_exchange_info_is_bound_but_never_historical_authority(
    v2_chain, tmp_path: Path
) -> None:
    parents = _parents(v2_chain, tmp_path)
    boundary = parents[2]
    current = tmp_path / "exchange-info-current.json"
    current.write_bytes(b'{"symbols":[]}')
    publication = publish_validation_precision_authority_v2(
        request=_request(
            boundary,
            PrecisionSourceKindV2.CURRENT_ONLY_EXCHANGE_INFO,
            limitation="Binance exchangeInfo is current-only and cannot backfill effective time.",
        ),
        coverage=parents[0],
        split=parents[1],
        boundary=boundary,
        availability=parents[3],
        minute_publication=parents[4],
        aggregate_publication=parents[5],
        authority_source_path=current,
        output_root=tmp_path / "precision-current",
    )

    assert publication.status is PrecisionAuthorityStatusV2.UNAVAILABLE
    assert publication.authority_source_sha256 == hashlib.sha256(current.read_bytes()).hexdigest()
    assert publication.signal_count == publication.outcome_rows == publication.final_rows == 0
    assert (
        precision_requirement_status_v2(publication) is PrecisionRequirementStatusV2.NOT_EVALUATED
    )
    with pytest.raises(ValueError, match="not_evaluated"):
        verified_effective_precision_v2(
            publication,
            symbol=boundary.allowed_symbols[0],
            at=boundary.allowed_intervals[0].start,
        )


def test_trusted_historical_effective_schedule_is_available_and_reopens_originals(
    v2_chain, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    parents = _parents(v2_chain, tmp_path)
    boundary = parents[2]
    source, verifier = _historical_evidence(tmp_path, boundary, monkeypatch)
    publication = publish_validation_precision_authority_v2(
        request=_request(
            boundary,
            PrecisionSourceKindV2.PUBLISHER_HISTORICAL_SCHEDULE,
            limitation=None,
        ),
        coverage=parents[0],
        split=parents[1],
        boundary=boundary,
        availability=parents[3],
        minute_publication=parents[4],
        aggregate_publication=parents[5],
        authority_source_path=source,
        verifier_evidence_path=verifier,
        output_root=tmp_path / "precision-historical",
    )

    assert publication.status is PrecisionAuthorityStatusV2.AVAILABLE
    assert precision_requirement_status_v2(publication) is PrecisionRequirementStatusV2.READY
    exact = verified_effective_precision_v2(
        publication,
        symbol=boundary.allowed_symbols[0],
        at=boundary.allowed_intervals[0].start,
    )
    assert exact.price_step == Decimal("0.01")
    assert exact.price_origin == Decimal("0")
    loaded = load_validation_precision_authority_v2(
        publication_root=publication.publication_root,
        expected_precision_authority_identity=publication.precision_authority_identity,
        coverage=parents[0],
        split=parents[1],
        boundary=boundary,
        availability=parents[3],
        minute_publication=parents[4],
        aggregate_publication=parents[5],
    )
    assert loaded.precision_authority_identity == publication.precision_authority_identity
    with pytest.raises((TypeError, ValueError)):
        replace(publication)
    source.write_bytes(b"{}")
    with pytest.raises(ValueError, match="source bytes changed"):
        precision_requirement_status_v2(publication)


def test_trusted_but_undercovered_schedule_is_incomplete_not_available(
    v2_chain, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    parents = _parents(v2_chain, tmp_path)
    boundary = parents[2]
    source, verifier = _historical_evidence(tmp_path, boundary, monkeypatch, omit_last=True)
    publication = publish_validation_precision_authority_v2(
        request=_request(
            boundary,
            PrecisionSourceKindV2.PUBLISHER_HISTORICAL_SCHEDULE,
            limitation=None,
        ),
        coverage=parents[0],
        split=parents[1],
        boundary=boundary,
        availability=parents[3],
        minute_publication=parents[4],
        aggregate_publication=parents[5],
        authority_source_path=source,
        verifier_evidence_path=verifier,
        output_root=tmp_path / "precision-incomplete",
    )
    assert publication.status is PrecisionAuthorityStatusV2.INCOMPLETE
    assert publication.failure == "effective_time_coverage_incomplete"
    assert publication.uncovered
    assert (
        precision_requirement_status_v2(publication) is PrecisionRequirementStatusV2.NOT_EVALUATED
    )
