from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from market_structure_lab.core.identity import (
    CanonicalIdentityError,
    canonical_json,
    evaluation_id,
    hash_canonical_json,
    hash_json,
    programme_id,
)


def test_hash_json_separates_domain_schema_and_field_boundaries() -> None:
    left = hash_json("programme", {"a": "ab", "b": "c"}, schema_version=1)
    right = hash_json("programme", {"a": "a", "b": "bc"}, schema_version=1)

    assert left != right
    assert left != hash_json("evaluation", {"a": "ab", "b": "c"}, schema_version=1)
    assert left != hash_json("programme", {"a": "ab", "b": "c"}, schema_version=2)


def test_validation_ids_are_stable_and_content_addressed() -> None:
    programme_payload = {"families": ["A", "B", "G", "E", "D"]}
    evaluation_payload = {"role": "primary", "slot": 1}

    first_programme = programme_id(programme_payload)
    first_evaluation = evaluation_id(first_programme, evaluation_payload)

    assert first_programme == programme_id(programme_payload)
    assert first_programme.startswith("VP-")
    assert len(first_programme) == 67
    assert first_evaluation == evaluation_id(first_programme, evaluation_payload)
    assert first_evaluation.startswith("VR-")
    assert len(first_evaluation) == 67
    assert first_evaluation != evaluation_id(
        programme_id({"families": ["A", "B"]}), evaluation_payload
    )


def test_canonical_json_normalises_utc_instants_and_rejects_naive_timestamps() -> None:
    utc = datetime(2026, 7, 22, 1, 2, 3, 4000, tzinfo=UTC)
    same_instant = utc.astimezone(timezone(timedelta(hours=7)))

    assert hash_json("row", {"timestamp": utc}) == hash_json("row", {"timestamp": same_instant})
    assert b"2026-07-22T01:02:03.004000Z" in canonical_json("row", {"timestamp": utc})
    with pytest.raises(CanonicalIdentityError, match="timezone-aware"):
        hash_json("row", {"timestamp": utc.replace(tzinfo=None)})


def test_integer_and_decimal_representations_are_explicit_and_finite() -> None:
    integer = hash_json("number", {"value": 1})
    decimal = hash_json("number", {"value": Decimal("1.0")})

    assert integer != decimal
    assert decimal == hash_json("number", {"value": Decimal("1.000")})
    assert decimal == hash_json("number", {"value": 1.0})
    for value in (float("inf"), float("-inf"), float("nan"), Decimal("NaN")):
        with pytest.raises(CanonicalIdentityError, match="finite"):
            hash_json("number", {"value": value})


@pytest.mark.parametrize(
    ("domain", "payload", "mutation"),
    (
        (
            "canonical-row",
            {"symbol": "APTUSDT", "open_time": datetime(2025, 2, 1, tzinfo=UTC), "row": 1},
            {"symbol": "APTUSDT", "open_time": datetime(2025, 2, 1, tzinfo=UTC), "row": 2},
        ),
        ("seed", {"programme": "VP-test", "seed": 7}, {"programme": "VP-test", "seed": 8}),
        (
            "holdout",
            {"role": "final", "start": datetime(2025, 6, 1, tzinfo=UTC)},
            {"role": "development", "start": datetime(2025, 6, 1, tzinfo=UTC)},
        ),
        (
            "baseline",
            {"role": "unconditional", "horizon_hours": 24},
            {"role": "persistence", "horizon_hours": 24},
        ),
        (
            "random-control",
            {"stratum": "APTUSDT:1h:long", "scalar": Decimal("0.25")},
            {"stratum": "APTUSDT:1h:long", "scalar": Decimal("0.26")},
        ),
    ),
)
def test_identity_payload_mutations_change_hash(
    domain: str, payload: dict[str, object], mutation: dict[str, object]
) -> None:
    assert hash_json(domain, payload) != hash_json(domain, mutation)


def test_canonical_json_rejects_unsupported_keys_and_unordered_sequences() -> None:
    with pytest.raises(CanonicalIdentityError, match="mapping keys"):
        canonical_json("invalid", {1: "value"})
    with pytest.raises(CanonicalIdentityError, match="ordered sequence"):
        canonical_json("invalid", {"values": {"A", "B"}})
    with pytest.raises(CanonicalIdentityError, match="mapping keys"):
        canonical_json("invalid", {"valid": 1, 2: "invalid"})


def test_one_serialized_byte_mutation_changes_or_invalidates_identity() -> None:
    encoded = canonical_json("canonical-row", {"symbol": "APTUSDT", "row": 1})
    original = hash_canonical_json(encoded)
    changed_value = encoded.replace(b"APTUSDT", b"IMXUSDT")

    assert hash_canonical_json(changed_value) != original

    whitespace_tamper = encoded[:-1] + b" \n}"
    with pytest.raises(CanonicalIdentityError, match="canonical"):
        hash_canonical_json(whitespace_tamper)


def test_hash_canonical_json_rejects_forged_nonfinite_decimal_or_timestamp() -> None:
    decimal = canonical_json("number", {"value": Decimal("1")})
    forged_decimal = decimal.replace(b'"value":"1"', b'"value":"NaN"')
    timestamp = canonical_json("row", {"timestamp": datetime(2026, 7, 22, tzinfo=UTC)})
    forged_timestamp = timestamp.replace(b"2026-07-22T00:00:00.000000Z", b"not-a-timestamp")

    with pytest.raises(CanonicalIdentityError, match="finite"):
        hash_canonical_json(forged_decimal)
    with pytest.raises(CanonicalIdentityError, match="timestamp"):
        hash_canonical_json(forged_timestamp)


def test_canonical_json_is_sorted_compact_utf8_json() -> None:
    encoded = canonical_json("programme", {"z": "last", "a": "first"})

    assert encoded == json.dumps(
        json.loads(encoded), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
