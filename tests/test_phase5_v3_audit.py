from __future__ import annotations

import hashlib
import json
from pathlib import Path


_ROOT = Path(__file__).parents[1]
_AUDIT = _ROOT / "docs/PHASE5_V3_PREREQUISITE_AND_FEASIBILITY_AUDIT.json"


def test_phase5_v3_prerequisite_audit_is_canonical_and_truthful() -> None:
    content = _AUDIT.read_bytes()
    payload = json.loads(content)

    assert (
        content
        == (
            json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
        ).encode()
    )
    audit_sha256 = payload.pop("audit_sha256")
    expected = hashlib.sha256(
        (
            json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
        ).encode()
    ).hexdigest()
    assert audit_sha256 == expected

    closure = payload["closure"]
    assert closure["funnel"] == {
        "failed_not_evaluated": 1_104,
        "final_eligible": 0,
        "frozen_slots": 1_104,
        "inconclusive": 0,
        "promoted": 0,
        "rejected": 0,
        "scientifically_evaluated": 0,
        "supported_development": 0,
        "terminal_slots": 1_104,
        "validated": 0,
    }
    assert closure["final_holdout_marker"] == "final_holdout_not_opened_empty_batch"
    assert (
        closure["final_access_attempts"],
        closure["final_rows"],
        closure["final_access_records"],
    ) == (0, 0, 0)
    assert closure["unavailable_parents"] == [
        "aggregate_publications",
        "event_level_cost_evidence",
        "profile_price_precision_evidence",
    ]
    assert payload["cost_contract_classification"] == "A_implementation_over_constraint"
    assert payload["capacity_semantics"] == "promotion_only"
    assert payload["family_b_precision_semantics"] == "family_local_unevaluable_p_equals_1"
    assert payload["successor_readiness"] == {
        "common_external_blockers": ["source_trust_root"],
        "local_implementation_blockers": [
            "v2_cost_policy_authority",
            "complete_1104_slot_implementation",
        ],
        "phase6": "closed",
        "phase7": "closed",
        "ready": False,
        "status": "no_ready_successor",
        "successor_selected": False,
    }


def test_phase5_v3_audit_covers_required_prerequisites_and_classifications() -> None:
    payload = json.loads(_AUDIT.read_bytes())
    prerequisites = {item["name"]: item for item in payload["prerequisites"]}
    assert {
        "source_trust_root",
        "development_minute_publication",
        "aggregate_1h_4h_publication",
        "family_b_historical_precision",
        "fee_policy",
        "spread",
        "slippage",
        "fill_probability",
        "latency",
        "missed_fills",
        "turnover",
        "capacity",
        "legal_unconditional_donors",
        "legal_persistence_donors",
        "delay_paths",
        "complete_1104_slot_implementation",
        "family_local_holm",
        "MSL-P5-SR-001",
        "untouched_split_and_holdout",
        "final_access_authority",
        "v2_cost_policy_authority",
    } == set(prerequisites)
    assert prerequisites["capacity"]["classification"] == "promotion-only"
    assert prerequisites["family_b_historical_precision"]["scope"] == "family-B-local"
    assert prerequisites["complete_1104_slot_implementation"]["classification"] == (
        "implementation defect"
    )
    assert prerequisites["v2_cost_policy_authority"]["classification"] == ("implementation defect")
