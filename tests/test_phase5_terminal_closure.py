from __future__ import annotations

from dataclasses import fields, replace
import hashlib
import json
import os
from pathlib import Path
from types import MappingProxyType
import pytest

from market_structure_lab.research.models import VALIDATION_SLOT_ROSTER
from market_structure_lab.research.phase5_terminal import (
    Phase5TerminalClosure,
    load_phase5_terminal_closure,
    publish_phase5_terminal_closure,
    verify_phase5_terminal_closure,
)

_ROOT = Path(__file__).parents[1]
_CONFIG = _ROOT / "configs/phase5/phase5-validation-programme-v1.json"
_PROGRAMME_ID = "VP-0d65fef04ca44dfc5ba7c7705e0be197480d7456b51178702a0011a18ab4487d"
_REAL_PROGRAMME_ROOT = (
    _ROOT / "data/exports/validation-programmes" / f"programme_id={_PROGRAMME_ID}"
)
_REAL_PUBLICATION = _ROOT / "data/exports/phase5-terminal/terminal_state=no-validated-edge-v3"


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _terminal_payloads() -> tuple[MappingProxyType[str, object], ...]:
    return tuple(
        MappingProxyType(
            {
                "slot_id": slot.slot_id,
                "decision": "not_evaluated",
                "execution_status": "failed",
                "computation_completed": False,
                "p_value": None,
            }
        )
        for slot in VALIDATION_SLOT_ROSTER
    )


def _patch_verified_parents(
    monkeypatch: pytest.MonkeyPatch,
    *,
    tree_snapshots: list[tuple[tuple[str, str, int], ...]],
) -> None:
    from market_structure_lab.research import phase5_terminal as module

    snapshots = iter(tree_snapshots)
    monkeypatch.setattr(
        module,
        "_tree_snapshot",
        lambda _root: (
            (snapshot := next(snapshots)),
            _sha256(repr(snapshot).encode()),
        ),
    )
    monkeypatch.setattr(
        module,
        "_legacy_terminal_snapshot",
        lambda _root, _config: (
            module._TerminalSnapshot(  # noqa: SLF001
                programme_id=_PROGRAMME_ID,
                runner_version="phase5-validation-programme-v1",
                receipt_parent_sha256="2" * 64,
                terminal_payloads=_terminal_payloads(),
                receipt_count=1_104,
                unavailable_prerequisites=(
                    "aggregate_publications",
                    "event_level_cost_evidence",
                    "profile_price_precision_evidence",
                ),
            ),
            {
                "final_access_attempts": 0,
                "final_rows": 0,
                "final_access_records": 0,
            },
        ),
    )


def test_terminal_closure_rejects_non_frozen_caller_config(tmp_path: Path) -> None:
    config = tmp_path / "config.json"
    config.write_text(json.dumps({"programme_id": "TVPV2-" + "1" * 64}))
    programme = tmp_path / "receipts"
    programme.mkdir()

    with pytest.raises(ValueError, match="frozen parent"):
        publish_phase5_terminal_closure(programme, config, tmp_path / "closure")


def test_terminal_closure_publication_is_no_clobber(tmp_path: Path) -> None:
    output = tmp_path / "closure"
    output.mkdir()
    sentinel = output / "sentinel"
    sentinel.write_bytes(b"owned\n")

    with pytest.raises(FileExistsError):
        publish_phase5_terminal_closure(tmp_path, _CONFIG, output)

    assert sentinel.read_bytes() == b"owned\n"


def test_terminal_closure_rechecks_parent_generation_before_factory_issuance(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = (("receipt.json", "a" * 64, 10),)
    changed = (("receipt.json", "b" * 64, 10),)
    _patch_verified_parents(monkeypatch, tree_snapshots=[first, first, changed])
    programme = tmp_path / "receipts"
    programme.mkdir()
    output = tmp_path / "closure"

    with pytest.raises(ValueError, match="parents changed"):
        publish_phase5_terminal_closure(programme, _CONFIG, output)

    assert not output.exists()


def test_terminal_closure_factory_and_exact_terminal_payload(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stable = (("receipt.json", "a" * 64, 10),)
    _patch_verified_parents(monkeypatch, tree_snapshots=[stable, stable, stable, stable, stable])
    programme = tmp_path / "receipts"
    programme.mkdir()
    closure = publish_phase5_terminal_closure(programme, _CONFIG, tmp_path / "closure")
    payload = closure.to_dict()

    assert payload["planned_slot_count"] == payload["terminal_slot_count"] == 1_104
    assert payload["eligible_candidate_ids"] == []
    assert len(payload["holm"]) == 64
    assert all(item["effective_p_value"] == 1.0 for item in payload["holm"])
    assert all(item["adjusted_holm_p_value"] == 1.0 for item in payload["holm"])
    assert not any(item["holm_reject"] for item in payload["holm"])
    assert len(payload["eligible_batch_sha256"]) == 64
    assert payload["final_holdout_marker"] == "final_holdout_not_opened_empty_batch"
    assert payload["final_access_attempts"] == payload["final_rows"] == 0
    assert payload["final_access_records"] == 0
    assert payload["phase6_status"] == "scientifically_gated_no_validated_edge"
    assert payload["phase7_status"] == "scientifically_gated_no_validated_edge"
    assert len(payload["implementation_sha256"]) == 64
    assert verify_phase5_terminal_closure(closure) is closure

    constructor = {field.name: getattr(closure, field.name) for field in fields(closure)}
    with pytest.raises(TypeError, match="publisher factory"):
        Phase5TerminalClosure(**constructor)  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="publisher factory"):
        replace(closure)


@pytest.mark.skipif(
    os.environ.get("MSL_RUN_PHASE5_TERMINAL_INTEGRATION") != "1"
    or not _REAL_PROGRAMME_ROOT.exists()
    or not _REAL_PUBLICATION.exists(),
    reason="requires the immutable local Phase 5 receipt and terminal evidence trees",
)
def test_real_terminal_closure_reopens_exact_original_parents() -> None:
    closure = load_phase5_terminal_closure(
        _REAL_PUBLICATION,
        expected_closure_sha256=json.loads((_REAL_PUBLICATION / "publication.json").read_bytes())[
            "closure_sha256"
        ],
        programme_root=_REAL_PROGRAMME_ROOT,
        config_path=_CONFIG,
    )

    assert closure.to_dict()["final_access_attempts"] == 0
