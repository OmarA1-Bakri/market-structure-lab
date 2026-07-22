"""Fail-closed boundary for independently published source price precision.

Canonical snapshots do not yet bind a source price-precision manifest or an
independently tracked authority checkpoint.  Until the snapshot publication
schema carries both, family B profile construction is deliberately unavailable.
"""

from __future__ import annotations

from dataclasses import dataclass

from market_structure_lab.data.aggregate_publication import VerifiedAggregateSeries


class SourcePricePrecisionUnavailable(ValueError):
    """The verified source publication lacks independent precision authority."""


@dataclass(frozen=True, slots=True)
class SourcePricePrecisionManifest:
    """Future independently published precision manifest contract.

    Instances are not accepted from callers.  The public reader will issue one
    only after the canonical snapshot schema binds both ``artifact_sha256`` and
    ``authority_checkpoint`` into its verified publication identity.
    """

    artifact_sha256: str
    manifest_sha256: str
    authority_checkpoint: str
    source_snapshot_sha256: str
    source_mapping_version: str
    symbol: str
    binning_version: str
    step: float
    origin: float
    artifact_bytes: int


def read_source_price_precision_manifest(
    series: VerifiedAggregateSeries,
) -> SourcePricePrecisionManifest:
    """Read independently authorized precision, or fail closed when unbound.

    ``SnapshotIdentity`` currently has no source-precision artifact digest and
    no independently fixed authority checkpoint.  A validation-programme hash
    is intentionally insufficient because the same caller can mint a new VP.
    """

    if not isinstance(series, VerifiedAggregateSeries):
        raise TypeError("source price precision requires a verified aggregate series")
    raise SourcePricePrecisionUnavailable(
        "family B is unavailable: the verified snapshot has no independently tracked "
        "source price-precision artifact and authority checkpoint binding"
    )


__all__ = [
    "SourcePricePrecisionManifest",
    "SourcePricePrecisionUnavailable",
    "read_source_price_precision_manifest",
]
