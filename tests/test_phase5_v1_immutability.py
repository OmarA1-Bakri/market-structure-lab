from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest


_V1_CONFIG_BYTES = {
    "phase5-validation-bindings-v1.json": (
        "57455e4af1aadbc680172ee2e373cce186bf9544b0795355fc6706bb6689745a"
    ),
    "phase5-validation-programme-v1.json": (
        "376eac4c05df279fdeba35b63abebef670409ec566577981fd35b5861cfdd1b7"
    ),
    "phase5-validation-source-preflight-v1.json": (
        "b96036e541c198762ee5cdfd43175ba154fcde5084f56da6a88c52ee2780a463"
    ),
}
_V1_PROGRAMME_ID = (
    "VP-0d65fef04ca44dfc5ba7c7705e0be197480d7456b51178702a0011a18ab4487d"
)
_V1_CONFIG_SHA256 = "f98bd334bd8e360d274ad4aad6df0f3336cf6acc9f5c94c7328cab68866f1561"


@pytest.mark.parametrize(("name", "expected_sha256"), _V1_CONFIG_BYTES.items())
def test_phase5_v1_config_bytes_are_immutable(name: str, expected_sha256: str) -> None:
    path = Path("configs/phase5") / name

    assert hashlib.sha256(path.read_bytes()).hexdigest() == expected_sha256


def test_phase5_v1_documented_programme_identity_is_immutable() -> None:
    payload = json.loads(
        Path("configs/phase5/phase5-validation-programme-v1.json").read_text(
            encoding="utf-8"
        )
    )

    assert payload["programme_id"] == _V1_PROGRAMME_ID
    assert payload["config_sha256"] == _V1_CONFIG_SHA256


def test_v1_receipt_count_is_not_v2_slot_computation_evidence() -> None:
    """A V1 receipt is a wrapper, not a distinct V2 slot-computation result."""

    v1_receipt = {
        "schema_version": "validation-evaluation-receipt-v1",
        "evaluation_id": "VR-" + "1" * 64,
    }

    assert "slot_attempt_sha256" not in v1_receipt
    assert "slot_computation_result_sha256" not in v1_receipt
    assert v1_receipt["schema_version"] != "validation-slot-computation-result-v2"
