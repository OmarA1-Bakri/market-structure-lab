from __future__ import annotations

from datetime import UTC, datetime

import pytest

from market_structure_lab.data.reconciliation import (
    ReconciliationWorkUnit,
    TradingEnvelope,
    freeze_reconciliation_run,
)


def _sha(character: str) -> str:
    return character * 64


def test_freeze_reconciliation_run_is_canonical_and_complete() -> None:
    envelope = TradingEnvelope("BTCUSDT", "1m", 0, 120_000)
    units = (
        ReconciliationWorkUnit.create("BTCUSDT", "1m", 60_000, 120_000),
        ReconciliationWorkUnit.create("BTCUSDT", "1m", 0, 60_000),
    )

    first = freeze_reconciliation_run(
        run_id="RR-000001",
        cutoff=datetime(2026, 7, 16, tzinfo=UTC),
        dump_sha256=_sha("a"),
        source_row_count=35_748_117,
        mapping_version="market-data-candles-v1",
        candidate_venue="binance",
        market_type="spot",
        source_revision="binance-public-data-v1",
        algorithm_version="row-reconciliation-v1",
        code_commit="aedd375",
        uv_lock_sha256=_sha("b"),
        envelopes=(envelope,),
        work_units=units,
    )
    second = freeze_reconciliation_run(
        run_id="RR-000001",
        cutoff=datetime(2026, 7, 16, tzinfo=UTC),
        dump_sha256=_sha("a"),
        source_row_count=35_748_117,
        mapping_version="market-data-candles-v1",
        candidate_venue="binance",
        market_type="spot",
        source_revision="binance-public-data-v1",
        algorithm_version="row-reconciliation-v1",
        code_commit="aedd375",
        uv_lock_sha256=_sha("b"),
        envelopes=(envelope,),
        work_units=tuple(reversed(units)),
    )

    assert first == second
    assert first.work_units[0].start_ms == 0
    assert first.cutoff == "2026-07-16T00:00:00Z"
    assert len(first.manifest_sha256) == 64
    assert first.sha256() == first.manifest_sha256


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"run_id": "run-1"}, "RR-######"),
        ({"dump_sha256": "bad"}, "dump_sha256"),
        ({"source_row_count": -1}, "source_row_count"),
        ({"code_commit": "xyz"}, "code_commit"),
        ({"uv_lock_sha256": "bad"}, "uv_lock_sha256"),
        ({"candidate_venue": ""}, "candidate_venue"),
    ],
)
def test_freeze_reconciliation_run_rejects_invalid_identity(
    overrides: dict[str, object],
    message: str,
) -> None:
    values: dict[str, object] = {
        "run_id": "RR-000001",
        "cutoff": datetime(2026, 7, 16, tzinfo=UTC),
        "dump_sha256": _sha("a"),
        "source_row_count": 1,
        "mapping_version": "mapping-v1",
        "candidate_venue": "binance",
        "market_type": "spot",
        "source_revision": "revision-v1",
        "algorithm_version": "algorithm-v1",
        "code_commit": "aedd375",
        "uv_lock_sha256": _sha("b"),
        "envelopes": (TradingEnvelope("BTCUSDT", "1m", 0, 60_000),),
        "work_units": (ReconciliationWorkUnit.create("BTCUSDT", "1m", 0, 60_000),),
    }
    values.update(overrides)

    with pytest.raises(ValueError, match=message):
        freeze_reconciliation_run(**values)  # type: ignore[arg-type]


def test_envelopes_must_be_unique_and_cover_every_work_unit() -> None:
    envelope = TradingEnvelope("BTCUSDT", "1m", 0, 60_000)
    common = {
        "run_id": "RR-000001",
        "cutoff": datetime(2026, 7, 16, tzinfo=UTC),
        "dump_sha256": _sha("a"),
        "source_row_count": 1,
        "mapping_version": "mapping-v1",
        "candidate_venue": "binance",
        "market_type": "spot",
        "source_revision": "revision-v1",
        "algorithm_version": "algorithm-v1",
        "code_commit": "aedd375",
        "uv_lock_sha256": _sha("b"),
    }

    with pytest.raises(ValueError, match="duplicate"):
        freeze_reconciliation_run(
            **common,
            envelopes=(envelope, envelope),
            work_units=(ReconciliationWorkUnit.create("BTCUSDT", "1m", 0, 60_000),),
        )
    with pytest.raises(ValueError, match="inside"):
        freeze_reconciliation_run(
            **common,
            envelopes=(envelope,),
            work_units=(ReconciliationWorkUnit.create("ETHUSDT", "1m", 0, 60_000),),
        )
