"""Verified source price-precision metadata bound to one canonical snapshot symbol."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import InitVar, dataclass, field
import hashlib
import json
from math import isfinite
import re
from typing import cast

from market_structure_lab.core.identity import hash_json
from market_structure_lab.data.aggregate_publication import VerifiedAggregateSeries

_SHA256 = re.compile(r"^[a-f0-9]{64}$")
_PRICE_PRECISION_RECEIPT_SEAL = object()


@dataclass(frozen=True, slots=True)
class VerifiedSourcePricePrecision:
    """Data-layer receipt for pinned metadata belonging to one source snapshot/symbol."""

    artifact_sha256: str
    source_snapshot_sha256: str
    source_snapshot_identity_sha256: str
    aggregate_series_sha256: str
    symbol: str
    binning_version: str
    step: float
    origin: float
    receipt_sha256: str = field(init=False)
    seal: InitVar[object] = None

    def __post_init__(self, seal: object) -> None:
        if seal is not _PRICE_PRECISION_RECEIPT_SEAL:
            raise TypeError("price precision receipt requires the data-layer verifier seal")
        for name in (
            "artifact_sha256",
            "source_snapshot_sha256",
            "source_snapshot_identity_sha256",
            "aggregate_series_sha256",
        ):
            if (
                not isinstance(getattr(self, name), str)
                or _SHA256.fullmatch(getattr(self, name)) is None
            ):
                raise ValueError(f"{name} must be a lower-case SHA-256")
        if not self.symbol:
            raise ValueError("price precision symbol must not be empty")
        if self.binning_version != "fixed-step-v1":
            raise ValueError("unsupported price precision binning version")
        if not isfinite(self.step) or self.step <= 0:
            raise ValueError("price precision step must be finite and positive")
        if not isfinite(self.origin):
            raise ValueError("price precision origin must be finite")
        object.__setattr__(
            self,
            "receipt_sha256",
            hash_json("verified-source-price-precision-v1", self.to_dict()),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "artifact_sha256": self.artifact_sha256,
            "source_snapshot_sha256": self.source_snapshot_sha256,
            "source_snapshot_identity_sha256": self.source_snapshot_identity_sha256,
            "aggregate_series_sha256": self.aggregate_series_sha256,
            "symbol": self.symbol,
            "binning_version": self.binning_version,
            "step": self.step,
            "origin": self.origin,
        }


def verify_source_price_precision(
    series: VerifiedAggregateSeries,
    artifact_bytes: bytes,
    *,
    expected_artifact_sha256: str,
) -> VerifiedSourcePricePrecision:
    """Verify canonical metadata against a pinned hash and exact source snapshot identity."""

    if not isinstance(series, VerifiedAggregateSeries):
        raise TypeError("price precision verification requires a verified aggregate series")
    if (
        not isinstance(expected_artifact_sha256, str)
        or _SHA256.fullmatch(expected_artifact_sha256) is None
    ):
        raise ValueError("expected price precision artifact must be a lower-case SHA-256")
    if not isinstance(artifact_bytes, bytes):
        raise TypeError("price precision artifact must be supplied as bytes")
    artifact_sha256 = hashlib.sha256(artifact_bytes).hexdigest()
    if artifact_sha256 != expected_artifact_sha256:
        raise ValueError("source price precision differs from the frozen programme hash")
    try:
        payload = json.loads(artifact_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("source price precision artifact is invalid JSON") from error
    if not isinstance(payload, dict) or artifact_bytes != _canonical_bytes(payload):
        raise ValueError("source price precision artifact is not exact canonical JSON")
    required = {
        "schema_version",
        "source_snapshot_sha256",
        "source_snapshot_identity_sha256",
        "symbol",
        "binning_version",
        "step",
        "origin",
    }
    if set(payload) != required:
        raise ValueError("source price precision artifact fields are invalid")
    snapshot_identity_sha256 = hash_json(
        "source-snapshot-identity-v1", series.parent_snapshot_manifest.identity.to_dict()
    )
    if (
        payload["schema_version"] != 1
        or payload["source_snapshot_sha256"] != series.parent_snapshot_manifest.snapshot_sha256
        or payload["source_snapshot_identity_sha256"] != snapshot_identity_sha256
        or payload["symbol"] != series.symbol
        or payload["binning_version"] != "fixed-step-v1"
        or isinstance(payload["step"], bool)
        or not isinstance(payload["step"], (int, float))
        or isinstance(payload["origin"], bool)
        or not isinstance(payload["origin"], (int, float))
    ):
        raise ValueError("source price precision does not belong to the verified snapshot symbol")
    return VerifiedSourcePricePrecision(
        artifact_sha256=artifact_sha256,
        source_snapshot_sha256=series.parent_snapshot_manifest.snapshot_sha256,
        source_snapshot_identity_sha256=snapshot_identity_sha256,
        aggregate_series_sha256=series.series_sha256,
        symbol=series.symbol,
        binning_version="fixed-step-v1",
        step=float(cast(float, payload["step"])),
        origin=float(cast(float, payload["origin"])),
        seal=_PRICE_PRECISION_RECEIPT_SEAL,
    )


def _canonical_bytes(payload: Mapping[str, object]) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


__all__ = ["VerifiedSourcePricePrecision", "verify_source_price_precision"]
