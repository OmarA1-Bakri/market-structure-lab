from __future__ import annotations

from argparse import Namespace
from pathlib import Path
from types import SimpleNamespace

import pytest

from market_structure_lab.cli import publish_validation_precision_authority_v2 as cli
from market_structure_lab.data.validation_precision_authority_v2 import (
    PrecisionAuthorityRequestV2,
    PrecisionAuthorityStatusV2,
    PrecisionSourceKindV2,
)
from market_structure_lab.research.validation_v2_models import (
    AggregatePublicationIdentityV2,
    PrecisionAuthorityIdentityV2,
)


def _args(tmp_path: Path, *, network: str = "disabled") -> Namespace:
    return Namespace(
        coverage_config=tmp_path / "coverage.json",
        split_config=tmp_path / "split.json",
        boundary_publication=tmp_path / "boundary.json",
        availability_root=tmp_path / "availability",
        minute_publication_root=tmp_path / "minute",
        minute_audit_root=tmp_path / "minute-audit",
        aggregate_publication_root=tmp_path / "aggregate",
        expected_aggregate_identity=f"AGGV2-{'a' * 64}",
        venue="binance",
        query_source="api.binance.com/api/v3/exchangeInfo",
        source_kind=PrecisionSourceKindV2.CURRENT_ONLY_EXCHANGE_INFO.value,
        retrieval_identity_sha256="b" * 64,
        checkpoint_identity_sha256="c" * 64,
        limitation="current-only exchangeInfo cannot backfill effective time",
        authority_source=tmp_path / "exchangeInfo.json",
        verifier_evidence=None,
        network=network,
        output_root=tmp_path / "precision",
    )


def test_cli_rejects_network_before_loading_any_parent(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="network disabled"):
        cli.run(_args(tmp_path, network="enabled"))


def test_cli_loads_exact_parents_and_publishes_zero_access_status(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    coverage = SimpleNamespace()
    split = SimpleNamespace()
    boundary = SimpleNamespace(
        allowed_symbols=("ADAUSDT",),
        allowed_intervals=(
            SimpleNamespace(
                start=__import__("datetime").datetime(
                    2025, 1, 1, tzinfo=__import__("datetime").UTC
                ),
                end=__import__("datetime").datetime(2025, 1, 2, tzinfo=__import__("datetime").UTC),
            ),
        ),
    )
    availability = SimpleNamespace()
    minute = SimpleNamespace()
    aggregate = SimpleNamespace()
    captured: dict[str, object] = {}
    monkeypatch.setattr(
        cli, "load_v2_boundary_publications", lambda **_: (coverage, split, boundary)
    )
    monkeypatch.setattr(cli, "load_scoped_source_availability_v2", lambda **_: availability)
    monkeypatch.setattr(cli, "load_validation_source_publication_v2", lambda **_: minute)

    def load_aggregate(**kwargs):
        assert kwargs["expected_aggregate_identity"] == AggregatePublicationIdentityV2(
            f"AGGV2-{'a' * 64}"
        )
        return aggregate

    monkeypatch.setattr(cli, "load_validation_aggregate_publication_v2", load_aggregate)

    def publish(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(
            status=PrecisionAuthorityStatusV2.UNAVAILABLE,
            precision_authority_identity=PrecisionAuthorityIdentityV2(f"PRCV2-{'d' * 64}"),
        )

    monkeypatch.setattr(cli, "publish_validation_precision_authority_v2", publish)

    assert cli.run(_args(tmp_path)) == 0
    request = captured["request"]
    assert isinstance(request, PrecisionAuthorityRequestV2)
    assert request.source_kind is PrecisionSourceKindV2.CURRENT_ONLY_EXCHANGE_INFO
    assert request.requested_symbols == ("ADAUSDT",)
    assert captured["minute_publication"] is minute
    assert captured["aggregate_publication"] is aggregate
    assert "signals=0 outcomes=0 final_rows=0" in capsys.readouterr().out
