"""Public canonical JSON and domain-separated content identities."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from enum import Enum
import hashlib
import json
import math
import re
from typing import Any, cast


class CanonicalIdentityError(ValueError):
    """A value cannot participate in a deterministic canonical identity."""


def canonical_json(
    domain: str,
    payload: object,
    *,
    schema_version: int = 1,
) -> bytes:
    """Encode a schema-versioned payload as domain-separated canonical JSON bytes."""

    _require_domain(domain)
    _require_schema_version(schema_version)
    envelope = {
        "domain": domain,
        "payload": _normalise(payload),
        "schema_version": schema_version,
    }
    return _encode_json(envelope)


def hash_json(
    domain: str,
    payload: object,
    *,
    schema_version: int = 1,
) -> str:
    """Return the SHA-256 of canonical domain-separated JSON."""

    return hashlib.sha256(
        canonical_json(domain, payload, schema_version=schema_version)
    ).hexdigest()


def hash_canonical_json(encoded: bytes) -> str:
    """Validate already-canonical identity bytes and return their SHA-256."""

    if not isinstance(encoded, bytes):
        raise CanonicalIdentityError("canonical JSON must be supplied as bytes")
    try:
        decoded: Any = json.loads(encoded.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise CanonicalIdentityError("canonical JSON bytes are invalid") from error
    if not isinstance(decoded, dict) or set(decoded) != {
        "domain",
        "payload",
        "schema_version",
    }:
        raise CanonicalIdentityError("canonical JSON envelope is invalid")
    _require_domain(decoded["domain"])
    _require_schema_version(decoded["schema_version"])
    _validate_normalised(decoded["payload"])
    if _encode_json(decoded) != encoded:
        raise CanonicalIdentityError("serialized bytes are not canonical JSON")
    return hashlib.sha256(encoded).hexdigest()


def programme_id(payload: object, *, schema_version: int = 1) -> str:
    """Return a stable content-addressed validation-programme identity."""

    return f"VP-{hash_json('validation-programme', payload, schema_version=schema_version)}"


def evaluation_id(
    programme_identity: str,
    payload: object,
    *,
    schema_version: int = 1,
) -> str:
    """Return a stable evaluation identity separated by its owning programme."""

    if not isinstance(programme_identity, str) or not programme_identity.startswith("VP-"):
        raise CanonicalIdentityError("evaluation identity requires a VP programme identity")
    digest = hash_json(
        "validation-evaluation",
        {"programme_id": programme_identity, "evaluation": payload},
        schema_version=schema_version,
    )
    return f"VR-{digest}"


def _normalise(value: object) -> dict[str, object]:
    if value is None:
        return {"type": "null", "value": None}
    if isinstance(value, bool):
        return {"type": "boolean", "value": value}
    if isinstance(value, Enum):
        return _normalise(value.value)
    if isinstance(value, int):
        return {"type": "integer", "value": str(value)}
    if isinstance(value, (Decimal, float)):
        return {"type": "decimal", "value": _canonical_decimal(value)}
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise CanonicalIdentityError("identity timestamps must be timezone-aware")
        utc = value.astimezone(UTC)
        return {
            "type": "utc_datetime",
            "value": utc.isoformat(timespec="microseconds").replace("+00:00", "Z"),
        }
    if isinstance(value, str):
        return {"type": "string", "value": value}
    if isinstance(value, Mapping):
        if any(not isinstance(key, str) for key in value):
            raise CanonicalIdentityError("canonical JSON mapping keys must be strings")
        items: list[list[object]] = []
        for key in sorted(value):
            items.append([key, _normalise(value[key])])
        return {"type": "mapping", "value": items}
    if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray, memoryview)):
        return {"type": "sequence", "value": [_normalise(item) for item in value]}
    if isinstance(value, (set, frozenset)):
        raise CanonicalIdentityError("canonical JSON requires an ordered sequence, not a set")
    raise CanonicalIdentityError(f"unsupported canonical identity value: {type(value).__name__}")


def _canonical_decimal(value: Decimal | float) -> str:
    if isinstance(value, float):
        if not math.isfinite(value):
            raise CanonicalIdentityError("identity decimals must be finite")
        decimal = Decimal(str(value))
    else:
        decimal = cast(Decimal, value)
    if not decimal.is_finite():
        raise CanonicalIdentityError("identity decimals must be finite")
    try:
        rendered = format(decimal, "f")
    except InvalidOperation as error:
        raise CanonicalIdentityError("identity decimals must be finite") from error
    if "." in rendered:
        rendered = rendered.rstrip("0").rstrip(".")
    if rendered in {"", "-0"}:
        return "0"
    return rendered


def _validate_normalised(value: object) -> None:
    if not isinstance(value, dict) or set(value) != {"type", "value"}:
        raise CanonicalIdentityError("canonical JSON payload encoding is invalid")
    value_type = value["type"]
    encoded_value = value["value"]
    if value_type == "null":
        if encoded_value is not None:
            raise CanonicalIdentityError("canonical null encoding is invalid")
        return
    if value_type == "boolean":
        if not isinstance(encoded_value, bool):
            raise CanonicalIdentityError("canonical boolean encoding is invalid")
        return
    if value_type == "string":
        if not isinstance(encoded_value, str):
            raise CanonicalIdentityError("canonical scalar encoding is invalid")
        return
    if value_type == "integer":
        if (
            not isinstance(encoded_value, str)
            or re.fullmatch(r"-?(?:0|[1-9][0-9]*)", encoded_value) is None
        ):
            raise CanonicalIdentityError("canonical integer encoding is invalid")
        if encoded_value == "-0":
            raise CanonicalIdentityError("canonical integer encoding is invalid")
        return
    if value_type == "decimal":
        if not isinstance(encoded_value, str):
            raise CanonicalIdentityError("canonical decimal encoding is invalid")
        try:
            canonical = _canonical_decimal(Decimal(encoded_value))
        except InvalidOperation as error:
            raise CanonicalIdentityError("identity decimals must be finite") from error
        if canonical != encoded_value:
            raise CanonicalIdentityError("canonical decimal encoding is invalid")
        return
    if value_type == "utc_datetime":
        if not isinstance(encoded_value, str) or not encoded_value.endswith("Z"):
            raise CanonicalIdentityError("canonical UTC timestamp encoding is invalid")
        try:
            timestamp = datetime.fromisoformat(encoded_value.replace("Z", "+00:00"))
        except ValueError as error:
            raise CanonicalIdentityError("canonical UTC timestamp encoding is invalid") from error
        canonical = (
            timestamp.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")
        )
        if canonical != encoded_value:
            raise CanonicalIdentityError("canonical UTC timestamp encoding is invalid")
        return
    if value_type == "sequence":
        if not isinstance(encoded_value, list):
            raise CanonicalIdentityError("canonical sequence encoding is invalid")
        for item in encoded_value:
            _validate_normalised(item)
        return
    if value_type == "mapping":
        if not isinstance(encoded_value, list):
            raise CanonicalIdentityError("canonical mapping encoding is invalid")
        keys: list[str] = []
        for item in encoded_value:
            if not isinstance(item, list) or len(item) != 2 or not isinstance(item[0], str):
                raise CanonicalIdentityError("canonical mapping item is invalid")
            keys.append(item[0])
            _validate_normalised(item[1])
        if keys != sorted(keys) or len(keys) != len(set(keys)):
            raise CanonicalIdentityError("canonical mapping keys are not uniquely sorted")
        return
    raise CanonicalIdentityError("canonical JSON contains an unknown scalar type")


def _require_domain(domain: object) -> None:
    if not isinstance(domain, str) or not domain or domain.strip() != domain:
        raise CanonicalIdentityError("identity domain must be a non-empty canonical string")


def _require_schema_version(schema_version: object) -> None:
    if (
        isinstance(schema_version, bool)
        or not isinstance(schema_version, int)
        or schema_version < 1
    ):
        raise CanonicalIdentityError("identity schema_version must be a positive integer")


def _encode_json(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


__all__ = [
    "CanonicalIdentityError",
    "canonical_json",
    "evaluation_id",
    "hash_canonical_json",
    "hash_json",
    "programme_id",
]
