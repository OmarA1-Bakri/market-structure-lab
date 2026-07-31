"""Exact outcome-blind Phase 5 candidate and comparator detectors."""

from __future__ import annotations

from collections import deque
from collections.abc import Callable, Mapping, Sequence
from dataclasses import InitVar, dataclass, field
from datetime import datetime, timedelta
import hashlib
from io import BytesIO
import json
from math import fsum, isfinite
from statistics import median
from typing import Any, Self, TypeAlias, cast
import weakref

import polars as pl

from market_structure_lab.core.artifact_io import read_bounded_regular
from market_structure_lab.core.identity import hash_json
from market_structure_lab.data.aggregate_bars import (
    CONTINUITY_ID,
    CanonicalAggregateBar,
    canonical_source_row_identity,
)
from market_structure_lab.data.aggregate_publication import VerifiedAggregateSeries
from market_structure_lab.data.aggregate_publication_v2 import (
    AggregatePublicationBudgetV2,
    VerifiedAggregateSeriesV2,
    verify_verified_aggregate_series_v2,
)
from market_structure_lab.data.canonical import CANONICAL_SCHEMA, validate_candle_frame
from market_structure_lab.data.export import read_snapshot_manifest, verify_snapshot
from market_structure_lab.data.price_precision import read_source_price_precision_manifest
from market_structure_lab.profiles import BinContribution, ProfileAccumulator, UniformAllocation
from market_structure_lab.profiles.binning import FixedStepBins
from market_structure_lab.profiles.models import Candle, ProfileSnapshot
from market_structure_lab.research.models import (
    FROZEN_A_SELECTOR_GRID,
    CandidateDefinition,
    ValidationProgrammeConfig,
    ValidationSlot,
    ValidationSlotKind,
    ValidationWorkBudgetViolation,
    VALIDATION_SLOT_ROSTER,
    candidate_definition_for_slot,
)

_SHA256_LENGTH = 64
_TIMEFRAME_HOURS = {"1h": 1, "4h": 4}
_MAX_PROFILE_PARENT_PARTITION_BYTES = 64 * 1024 * 1024
_MAX_PROFILE_PARENT_PARTITION_ROWS = 2_000
_FROZEN_PROFILE_SEAL = object()
_VERIFIED_PROFILE_STREAM_SEAL = object()
_CANDIDATE_SIGNAL_ISSUANCE_CAPABILITY = object()
_VERIFIED_CANDIDATE_SERIES_V2_FACTORY = object()
_AGGREGATE_SCHEMA_V2 = "phase5-validation-development-aggregates-v2"


@dataclass(frozen=True, slots=True)
class _CausalAggregateBarV2:
    """Causal V2 fields with an explicitly unavailable V1 provenance adapter."""

    timestamp: datetime
    bar_close: datetime
    symbol: str
    target_timeframe: str
    segment_id: int
    source_row_count: int
    open: float
    high: float
    low: float
    close: float
    volume: float
    schema_version: int = 1
    source_timeframe: str = "1m"
    continuity: str = CONTINUITY_ID
    source_row_ids: tuple[str, ...] = ()
    source_sha256: str | None = None
    parent_snapshot_sha256: str | None = None
    row_sha256: str | None = None

    def to_dict(self) -> dict[str, object]:
        """Serialize only after a caller supplies complete, valid V1 identities."""

        if (
            len(self.source_row_ids) != self.source_row_count
            or self.source_sha256 is None
            or self.parent_snapshot_sha256 is None
            or self.row_sha256 is None
        ):
            raise ValueError(
                "V1 serialization requires independently supplied complete source identities"
            )
        return CanonicalAggregateBar(
            schema_version=self.schema_version,
            timestamp=self.timestamp,
            bar_close=self.bar_close,
            symbol=self.symbol,
            source_timeframe=self.source_timeframe,
            target_timeframe=self.target_timeframe,
            segment_id=self.segment_id,
            continuity=self.continuity,
            open=self.open,
            high=self.high,
            low=self.low,
            close=self.close,
            volume=self.volume,
            source_row_count=self.source_row_count,
            source_row_ids=self.source_row_ids,
            source_sha256=self.source_sha256,
            parent_snapshot_sha256=self.parent_snapshot_sha256,
            row_sha256=self.row_sha256,
        ).to_dict()


@dataclass(frozen=True, slots=True, weakref_slot=True)
class VerifiedCandidateSeriesV2:
    """Factory-issued detector view of one still-verifiable V2 aggregate series."""

    bars: tuple[_CausalAggregateBarV2, ...]
    publication_sha256: str
    series_sha256: str
    symbol: str
    target_timeframe: str
    interval_index: int
    segment_id: int
    aggregate_schema_version: str
    aggregate_identity: str
    aggregate_budget_sha256: str
    _factory_token: InitVar[object | None] = None

    def __post_init__(self, _factory_token: object | None) -> None:
        if _factory_token is not _VERIFIED_CANDIDATE_SERIES_V2_FACTORY:
            raise TypeError("VerifiedCandidateSeriesV2 requires its verifier factory")
        if not self.bars:
            raise ValueError("verified candidate series must contain bars")
        _require_sha256(self.publication_sha256, "publication_sha256")
        _require_sha256(self.series_sha256, "series_sha256")
        if (
            not isinstance(self.aggregate_identity, str)
            or not self.aggregate_identity.startswith("AGGV2-")
        ):
            raise ValueError("verified candidate series aggregate identity is invalid")
        _require_sha256(
            self.aggregate_identity.removeprefix("AGGV2-"),
            "aggregate_identity",
        )
        _require_sha256(self.aggregate_budget_sha256, "aggregate_budget_sha256")
        if self.aggregate_schema_version != _AGGREGATE_SCHEMA_V2:
            raise ValueError("verified candidate series aggregate schema is invalid")

    def verify(self) -> Self:
        """Revalidate this bridge and its original aggregate bytes."""

        return cast(Self, verify_candidate_series_v2(self))


@dataclass(frozen=True, slots=True)
class _VerifiedCandidateSeriesV2Registration:
    candidate: weakref.ReferenceType[VerifiedCandidateSeriesV2]
    aggregate: VerifiedAggregateSeriesV2
    budget: AggregatePublicationBudgetV2
    snapshot: tuple[object, ...]


_VERIFIED_CANDIDATE_SERIES_V2: dict[int, _VerifiedCandidateSeriesV2Registration] = {}


DetectorSeries: TypeAlias = VerifiedAggregateSeries | VerifiedCandidateSeriesV2
DetectorBar: TypeAlias = CanonicalAggregateBar | _CausalAggregateBarV2


@dataclass(frozen=True, slots=True)
class _SignalIssuanceEntry:
    token: object
    capability: object
    payload_sha256: str


_SIGNAL_ISSUANCE_REGISTRY: dict[int, _SignalIssuanceEntry] = {}


A_SELECTOR_GRID = FROZEN_A_SELECTOR_GRID


@dataclass(frozen=True, slots=True)
class CandidateSignal:
    """One causal completed-bar signal issued once by the active detector closure."""

    signal_id: str = field(init=False)
    candidate_id: str
    family: str
    symbol: str
    timeframe: str
    direction: int
    feature_start: datetime
    information_cutoff: datetime
    legal_entry: datetime
    source_publication_sha256: str
    source_series_sha256: str
    segment_id: int
    candidate_slot_id: str
    issuance_token: InitVar[object]

    def __post_init__(self, issuance_token: object) -> None:
        payload = self.to_dict()
        entry = _SIGNAL_ISSUANCE_REGISTRY.pop(id(issuance_token), None)
        if (
            entry is None
            or entry.token is not issuance_token
            or entry.capability is not _CANDIDATE_SIGNAL_ISSUANCE_CAPABILITY
            or entry.payload_sha256 != hash_json("candidate-signal-issuance-v1", payload)
        ):
            raise TypeError("CandidateSignal requires a detector-owned one-use issuance token")
        if not self.candidate_id.startswith("HC-"):
            raise ValueError("candidate_id must identify a human-origin candidate")
        if self.family not in ("A", "B", "G", "E", "D"):
            raise ValueError("candidate signal family is unsupported")
        if self.timeframe not in ("1h", "4h"):
            raise ValueError("candidate signal timeframe must be 1h or 4h")
        if self.direction not in (-1, 1):
            raise ValueError("candidate signal direction must be -1 or +1")
        for name in ("feature_start", "information_cutoff", "legal_entry"):
            value = getattr(self, name)
            offset = value.utcoffset()
            if value.tzinfo is None or offset is None or offset.total_seconds():
                raise ValueError(f"{name} must be UTC-aware")
        if self.feature_start >= self.information_cutoff:
            raise ValueError("feature_start must precede the information cutoff")
        if self.legal_entry < self.information_cutoff:
            raise ValueError("legal_entry cannot precede the information cutoff")
        _require_sha256(self.source_publication_sha256, "source_publication_sha256")
        _require_sha256(self.source_series_sha256, "source_series_sha256")
        if isinstance(self.segment_id, bool) or self.segment_id < 0:
            raise ValueError("segment_id must be a non-negative integer")
        object.__setattr__(
            self,
            "signal_id",
            f"CS-{hash_json('candidate-signal-v1', payload)}",
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "candidate_id": self.candidate_id,
            "family": self.family,
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "direction": self.direction,
            "feature_start": self.feature_start,
            "information_cutoff": self.information_cutoff,
            "legal_entry": self.legal_entry,
            "source_publication_sha256": self.source_publication_sha256,
            "source_series_sha256": self.source_series_sha256,
            "segment_id": self.segment_id,
            "candidate_slot_id": self.candidate_slot_id,
        }


@dataclass(frozen=True, slots=True)
class ProfileValueReferences:
    """Compact integer-bin inputs for the pure family B acceptance formula."""

    poc_index: int | None
    value_area_low_index: int | None
    value_area_high_index: int | None
    value_area_mid_index: float | None


def value_migration_acceptance(
    previous: ProfileValueReferences,
    current: ProfileValueReferences,
    *,
    prior_close_bin: int,
    current_close_bin: int,
    direction: int,
) -> bool:
    """Evaluate B deterministically without issuing a candidate or claiming metadata authority."""

    if direction not in (-1, 1):
        raise ValueError("family B direction must be -1 or +1")
    references = (
        previous.poc_index,
        previous.value_area_low_index,
        previous.value_area_high_index,
        previous.value_area_mid_index,
        current.poc_index,
        current.value_area_low_index,
        current.value_area_high_index,
        current.value_area_mid_index,
    )
    if any(value is None for value in references):
        return False
    prior_poc = cast(int, previous.poc_index)
    poc = cast(int, current.poc_index)
    prior_mid = cast(float, previous.value_area_mid_index)
    midpoint = cast(float, current.value_area_mid_index)
    low = cast(int, current.value_area_low_index)
    high = cast(int, current.value_area_high_index)
    migration = (
        poc > prior_poc and midpoint > prior_mid
        if direction == 1
        else poc < prior_poc and midpoint < prior_mid
    )
    return migration and low <= prior_close_bin <= high and low <= current_close_bin <= high


@dataclass(frozen=True, slots=True)
class FrozenProfile:
    """A rolling one-minute profile reference frozen at a completed cutoff."""

    information_cutoff: datetime
    window_hours: int
    source_minute_publication_sha256: str
    profile_config_sha256: str
    bin_metadata_sha256: str
    segment_id: int
    policy: str
    bin_definition_id: str
    poc_index: int | None
    value_area_low_index: int | None
    value_area_high_index: int | None
    value_area_mid_index: float | None
    profile_content_sha256: str
    active_bin_cells: int
    serialized_bytes: int
    source_start_index: int
    source_end_index: int
    source_row_count: int
    source_start_timestamp: datetime
    source_end_timestamp: datetime
    source_first_row_id: str
    source_last_row_id: str
    source_window_sha256: str
    source_timeframe: str = "1m"
    profile_id: str = field(init=False)
    seal: InitVar[object] = None

    def __post_init__(self, seal: object) -> None:
        if seal is not _FROZEN_PROFILE_SEAL:
            raise TypeError("FrozenProfile requires the verified profile factory seal")
        offset = self.information_cutoff.utcoffset()
        if self.information_cutoff.tzinfo is None or offset is None or offset != timedelta(0):
            raise ValueError("profile cutoff must be UTC-aware")
        if isinstance(self.window_hours, bool) or self.window_hours < 1:
            raise ValueError("profile window_hours must be positive")
        _require_sha256(self.source_minute_publication_sha256, "profile source minute publication")
        _require_sha256(self.profile_config_sha256, "profile config")
        _require_sha256(self.bin_metadata_sha256, "profile bin metadata")
        if isinstance(self.segment_id, bool) or self.segment_id < 0:
            raise ValueError("profile segment_id must be non-negative")
        if self.policy != "rolling":
            raise ValueError("candidate profiles require the rolling policy")
        if self.source_timeframe != "1m":
            raise ValueError("candidate profiles must be constructed from one-minute bars")
        if not self.bin_definition_id.endswith(
            f"source=verified-price-precision:{self.bin_metadata_sha256}"
        ):
            raise ValueError("candidate profile binning lacks exact verified precision metadata")
        references = (self.poc_index, self.value_area_low_index, self.value_area_high_index)
        if any(value is not None and type(value) is not int for value in references):
            raise TypeError("candidate profile value references must be integer bin indices")
        if any(value is None for value in references) and any(
            value is not None for value in references
        ):
            raise ValueError("candidate profile value references must be complete or absent")
        if self.value_area_low_index is None:
            if self.value_area_mid_index is not None:
                raise ValueError("empty candidate profile cannot claim a value-area midpoint")
        elif (
            self.value_area_mid_index
            != (self.value_area_low_index + cast(int, self.value_area_high_index)) / 2
        ):
            raise ValueError("candidate profile midpoint differs from its value-area bounds")
        _require_sha256(self.profile_content_sha256, "profile content")
        if (
            isinstance(self.active_bin_cells, bool)
            or not isinstance(self.active_bin_cells, int)
            or isinstance(self.serialized_bytes, bool)
            or not isinstance(self.serialized_bytes, int)
            or self.active_bin_cells < 0
            or self.serialized_bytes < 1
        ):
            raise ValueError("candidate profile budget accounting is invalid")
        if (
            isinstance(self.source_start_index, bool)
            or isinstance(self.source_end_index, bool)
            or self.source_start_index < 0
            or self.source_end_index <= self.source_start_index
            or self.source_row_count != self.source_end_index - self.source_start_index
            or self.source_row_count != self.window_hours * 60
        ):
            raise ValueError("candidate profile source window indices are invalid")
        if self.source_end_timestamp != self.information_cutoff - timedelta(minutes=1):
            raise ValueError("candidate profile source window does not end at its cutoff")
        if self.source_start_timestamp != self.information_cutoff - timedelta(
            hours=self.window_hours
        ):
            raise ValueError("candidate profile source window does not start at its cutoff")
        _require_sha256(self.source_window_sha256, "profile source window")
        _require_sha256(self.source_first_row_id, "profile first source row")
        _require_sha256(self.source_last_row_id, "profile last source row")
        object.__setattr__(
            self,
            "profile_id",
            hash_json(
                "frozen-candidate-profile-v1",
                {
                    "information_cutoff": self.information_cutoff,
                    "feature_start": self.feature_start,
                    "window_hours": self.window_hours,
                    "source_minute_publication_sha256": self.source_minute_publication_sha256,
                    "profile_config_sha256": self.profile_config_sha256,
                    "bin_metadata_sha256": self.bin_metadata_sha256,
                    "segment_id": self.segment_id,
                    "policy": self.policy,
                    "source_timeframe": self.source_timeframe,
                    "source_start_index": self.source_start_index,
                    "source_end_index": self.source_end_index,
                    "source_row_count": self.source_row_count,
                    "source_start_timestamp": self.source_start_timestamp,
                    "source_end_timestamp": self.source_end_timestamp,
                    "source_window_sha256": self.source_window_sha256,
                    "bin_definition_id": self.bin_definition_id,
                    "poc_index": self.poc_index,
                    "value_area_low_index": self.value_area_low_index,
                    "value_area_high_index": self.value_area_high_index,
                    "value_area_mid_index": self.value_area_mid_index,
                    "profile_content_sha256": self.profile_content_sha256,
                    "active_bin_cells": self.active_bin_cells,
                    "serialized_bytes": self.serialized_bytes,
                    "source_first_row_id": self.source_first_row_id,
                    "source_last_row_id": self.source_last_row_id,
                },
            ),
        )

    @property
    def feature_start(self) -> datetime:
        return self.source_start_timestamp


@dataclass(frozen=True, slots=True)
class VerifiedProfileStream:
    """Unique ordered content receipt for deterministic rolling one-minute profiles."""

    aggregate_series_sha256: str
    validation_programme_id: str
    work_budget_sha256: str
    source_minute_publication_sha256: str
    profile_config_sha256: str
    bin_metadata_sha256: str
    bin_step: float
    bin_origin: float
    bin_definition_id: str
    source_price_precision_manifest_sha256: str
    window_hours: int
    source_row_count: int
    source_sha256: str
    profile_active_bin_cells: int
    profile_source_id_bytes: int
    profile_config_bytes: int
    profile_serialized_bytes: int
    profiles: tuple[FrozenProfile, ...]
    ordered_profile_ids: tuple[str, ...]
    stream_sha256: str
    seal: InitVar[object]

    def __post_init__(self, seal: object) -> None:
        if seal is not _VERIFIED_PROFILE_STREAM_SEAL:
            raise TypeError("VerifiedProfileStream requires its verifier capability seal")
        for name in (
            "aggregate_series_sha256",
            "work_budget_sha256",
            "source_minute_publication_sha256",
            "profile_config_sha256",
            "bin_metadata_sha256",
            "source_sha256",
            "source_price_precision_manifest_sha256",
        ):
            _require_sha256(getattr(self, name), name)
        cutoffs = tuple(profile.information_cutoff for profile in self.profiles)
        if cutoffs != tuple(sorted(cutoffs)) or len(cutoffs) != len(set(cutoffs)):
            raise ValueError("verified profile stream cutoffs must be unique and ordered")
        if self.ordered_profile_ids != tuple(profile.profile_id for profile in self.profiles):
            raise ValueError("verified profile stream identities differ from profile content")
        if not self.validation_programme_id.startswith("VP-"):
            raise ValueError("verified profile stream lacks its validation programme identity")
        if self.source_row_count < 1:
            raise ValueError("verified profile stream source row count is invalid")
        if self.profile_source_id_bytes != self.source_row_count * _SHA256_LENGTH:
            raise ValueError("verified profile stream source identity byte accounting is invalid")
        if self.profile_config_bytes < 1:
            raise ValueError("verified profile stream config byte accounting is invalid")
        if self.profile_active_bin_cells != sum(
            profile.active_bin_cells for profile in self.profiles
        ):
            raise ValueError("verified profile stream active-bin accounting is invalid")
        if self.profile_serialized_bytes != sum(
            profile.serialized_bytes for profile in self.profiles
        ):
            raise ValueError("verified profile stream serialized-byte accounting is invalid")
        for profile in self.profiles:
            if (
                profile.source_minute_publication_sha256 != self.source_minute_publication_sha256
                or profile.profile_config_sha256 != self.profile_config_sha256
                or profile.bin_metadata_sha256 != self.bin_metadata_sha256
                or profile.window_hours != self.window_hours
                or profile.bin_definition_id != self.bin_definition_id
            ):
                raise ValueError("profile content differs from the verified stream receipt")
            expected_window = _profile_window_sha256(
                source_sha256=self.source_sha256,
                start_index=profile.source_start_index,
                end_index=profile.source_end_index,
                first_source_row_id=profile.source_first_row_id,
                last_source_row_id=profile.source_last_row_id,
            )
            if profile.source_window_sha256 != expected_window:
                raise ValueError("profile source window differs from exact ordered parent rows")
        expected = hash_json(
            "verified-candidate-profile-stream-v1",
            {
                "aggregate_series_sha256": self.aggregate_series_sha256,
                "validation_programme_id": self.validation_programme_id,
                "work_budget_sha256": self.work_budget_sha256,
                "source_minute_publication_sha256": self.source_minute_publication_sha256,
                "profile_config_sha256": self.profile_config_sha256,
                "bin_metadata_sha256": self.bin_metadata_sha256,
                "bin_step": self.bin_step,
                "bin_origin": self.bin_origin,
                "bin_definition_id": self.bin_definition_id,
                "source_price_precision_manifest_sha256": (
                    self.source_price_precision_manifest_sha256
                ),
                "window_hours": self.window_hours,
                "source_row_count": self.source_row_count,
                "source_sha256": self.source_sha256,
                "profile_active_bin_cells": self.profile_active_bin_cells,
                "profile_source_id_bytes": self.profile_source_id_bytes,
                "profile_config_bytes": self.profile_config_bytes,
                "profile_serialized_bytes": self.profile_serialized_bytes,
                "ordered_profile_ids": self.ordered_profile_ids,
            },
        )
        if self.stream_sha256 != expected:
            raise ValueError("verified profile stream identity mismatch")

    def bin_index(self, price: float) -> int:
        """Map a price through the exact pinned precision without retaining profile maps."""

        return FixedStepBins(
            step=self.bin_step,
            origin=self.bin_origin,
            provenance=f"verified-price-precision:{self.bin_metadata_sha256}",
        ).bin_index(price)


def profile_config_artifact_bytes() -> bytes:
    """Return the one exact rolling-profile registry for every legal B window."""

    return _artifact_bytes(
        {
            "schema_version": 2,
            "policy": "rolling",
            "source_timeframe": "1m",
            "allocation_id": "uniform-touched-v1",
            "value_area_fraction": 0.70,
            "window_hours": list(_frozen_profile_window_hours()),
        }
    )


def build_verified_profile_stream(
    series: VerifiedAggregateSeries,
    programme: ValidationProgrammeConfig,
    *,
    window_hours: int,
    profile_config_bytes: bytes,
) -> VerifiedProfileStream:
    """Build rolling profiles only from exact verified parent one-minute publication rows."""

    if not isinstance(series, VerifiedAggregateSeries):
        raise TypeError("profile builder requires a VerifiedAggregateSeries capability")
    if not isinstance(programme, ValidationProgrammeConfig):
        raise TypeError("profile builder requires its frozen ValidationProgrammeConfig")
    if programme.dataset_sha256 != series.parent_snapshot_manifest.snapshot_sha256:
        raise ValueError("validation programme dataset does not match the profile source snapshot")
    price_precision_manifest = read_source_price_precision_manifest(series)
    if hashlib.sha256(profile_config_bytes).hexdigest() != programme.profile_config_sha256:
        raise ValueError("profile config artifact bytes differ from the frozen expectation")
    if price_precision_manifest.artifact_sha256 != programme.source_price_precision_sha256:
        raise ValueError("source price precision differs from the frozen programme hash")
    if (
        price_precision_manifest.source_snapshot_sha256
        != series.parent_snapshot_manifest.snapshot_sha256
        or price_precision_manifest.symbol != series.symbol
        or price_precision_manifest.source_mapping_version
        != series.parent_snapshot_manifest.identity.mapping_version
    ):
        raise ValueError("price precision manifest does not own the verified snapshot symbol")
    budget = programme.work_budget
    config_bytes = len(profile_config_bytes) + price_precision_manifest.artifact_bytes
    _require_profile_budget("profile_config_bytes", config_bytes, budget.max_profile_config_bytes)
    config = _parse_artifact(profile_config_bytes, "profile config")
    expected_config_fields = {
        "schema_version",
        "policy",
        "source_timeframe",
        "allocation_id",
        "value_area_fraction",
        "window_hours",
    }
    if set(config) != expected_config_fields or profile_config_bytes != _artifact_bytes(config):
        raise ValueError("profile config artifact bytes are not exact canonical JSON")
    if (
        config["schema_version"] != 2
        or config["policy"] != "rolling"
        or config["source_timeframe"] != "1m"
        or config["allocation_id"] != "uniform-touched-v1"
        or config["value_area_fraction"] != 0.70
        or config["window_hours"] != list(_frozen_profile_window_hours())
        or isinstance(window_hours, bool)
        or not isinstance(window_hours, int)
        or window_hours not in _frozen_profile_window_hours()
    ):
        raise ValueError("profile config artifact differs from the frozen rolling policy")
    profile_config_sha256 = hashlib.sha256(profile_config_bytes).hexdigest()
    bin_metadata_sha256 = price_precision_manifest.artifact_sha256
    bin_step = price_precision_manifest.step
    bin_origin = price_precision_manifest.origin
    binning = FixedStepBins(
        step=bin_step,
        origin=bin_origin,
        provenance=f"verified-price-precision:{bin_metadata_sha256}",
    )
    expected_cutoffs = tuple(
        bar.bar_close
        for bar in series.bars
        if bar.bar_close - series.bars[0].timestamp >= timedelta(hours=window_hours)
    )
    source_id_bytes = series.manifest.source_row_count * _SHA256_LENGTH
    _require_profile_budget(
        "profile_stream_count", len(expected_cutoffs), budget.max_profile_stream_count
    )
    _require_profile_budget(
        "profile_source_id_bytes", source_id_bytes, budget.max_profile_source_id_bytes
    )
    _require_profile_budget(
        "profile_serialized_bytes",
        len(expected_cutoffs),
        budget.max_profile_serialized_bytes,
    )
    verify_snapshot(series.parent_snapshot_directory, series.parent_snapshot_manifest)
    if (
        read_snapshot_manifest(series.parent_snapshot_directory / "manifest.json")
        != series.parent_snapshot_manifest
    ):
        raise ValueError("profile parent snapshot manifest changed after verification")
    accumulator = ProfileAccumulator(
        binning=binning,
        allocation=UniformAllocation(),
        value_area_fraction=0.70,
    )
    active: deque[BinContribution] = deque()
    active_source_ids: deque[str] = deque()
    profiles: list[FrozenProfile] = []
    cutoff_set = {bar.bar_close for bar in series.bars}
    expected_timestamp = series.bars[0].timestamp
    source_index = 0
    active_bin_cells = 0
    serialized_bytes = 0
    expected_source_ids = iter(source_id for bar in series.bars for source_id in bar.source_row_ids)
    expected_schema = pl.Schema(cast(Any, {**CANONICAL_SCHEMA, "segment_id": pl.UInt64}))
    for path, expected_sha256 in series.manifest.parent_partition_bindings:
        content = read_bounded_regular(
            series.parent_snapshot_directory / path,
            _MAX_PROFILE_PARENT_PARTITION_BYTES,
        )
        if hashlib.sha256(content).hexdigest() != expected_sha256:
            raise ValueError("profile parent partition differs from aggregate lineage")
        frame = pl.read_parquet(BytesIO(content))
        if frame.schema != expected_schema or frame.height > _MAX_PROFILE_PARENT_PARTITION_ROWS:
            raise ValueError("profile parent partition schema or row bound is invalid")
        validate_candle_frame(frame)
        selected = frame.filter(
            (pl.col("symbol") == series.symbol)
            & (pl.col("timeframe") == "1m")
            & (pl.col("segment_id") == series.segment_id)
        )
        for row in selected.iter_rows(named=True):
            timestamp = cast(datetime, row["timestamp"])
            if source_index >= series.manifest.source_row_count:
                raise ValueError("profile parent contains extra selected source rows")
            if timestamp != expected_timestamp:
                raise ValueError("profile parent minute rows are missing, duplicated, or reordered")
            observed_id = canonical_source_row_identity(row)
            try:
                expected_source_id = next(expected_source_ids)
            except StopIteration as error:
                raise ValueError(
                    "aggregate source identities ended before the parent rows"
                ) from error
            if observed_id != expected_source_id:
                raise ValueError("profile parent row differs from aggregate source identity")
            contribution = accumulator.add(
                Candle(
                    open=float(cast(float, row["open"])),
                    high=float(cast(float, row["high"])),
                    low=float(cast(float, row["low"])),
                    close=float(cast(float, row["close"])),
                    volume=float(cast(float, row["volume"])),
                )
            )
            active.append(contribution)
            active_source_ids.append(observed_id)
            window_rows = window_hours * 60
            if len(active) > window_rows:
                accumulator.remove(active.popleft())
                active_source_ids.popleft()
            cutoff = timestamp + timedelta(minutes=1)
            if cutoff in cutoff_set and len(active) == window_rows:
                start_index = source_index - window_rows + 1
                end_index = source_index + 1
                next_active_cells = active_bin_cells + accumulator.active_bin_count
                _require_profile_budget(
                    "profile_active_bin_cells",
                    next_active_cells,
                    budget.max_profile_active_bin_cells,
                )
                snapshot = accumulator.snapshot()
                first_source_row_id = active_source_ids[0]
                last_source_row_id = active_source_ids[-1]
                source_start_timestamp = timestamp - timedelta(minutes=window_rows - 1)
                source_window_sha256 = _profile_window_sha256(
                    source_sha256=series.manifest.source_sha256,
                    start_index=start_index,
                    end_index=end_index,
                    first_source_row_id=first_source_row_id,
                    last_source_row_id=last_source_row_id,
                )
                content_sha256 = hash_json(
                    "candidate-profile-content-v1", _profile_payload(snapshot)
                )
                compact_payload = _compact_profile_payload(
                    information_cutoff=cutoff,
                    window_hours=window_hours,
                    source_minute_publication_sha256=series.manifest.parent_snapshot_sha256,
                    profile_config_sha256=profile_config_sha256,
                    bin_metadata_sha256=bin_metadata_sha256,
                    segment_id=series.segment_id,
                    bin_definition_id=binning.definition_id,
                    source_start_index=start_index,
                    source_end_index=end_index,
                    source_row_count=window_rows,
                    source_start_timestamp=source_start_timestamp,
                    source_end_timestamp=timestamp,
                    source_first_row_id=first_source_row_id,
                    source_last_row_id=last_source_row_id,
                    source_window_sha256=source_window_sha256,
                    profile_content_sha256=content_sha256,
                    snapshot=snapshot,
                )
                item_bytes = len(_artifact_bytes(compact_payload))
                next_serialized_bytes = serialized_bytes + item_bytes
                _require_profile_budget(
                    "profile_serialized_bytes",
                    next_serialized_bytes,
                    budget.max_profile_serialized_bytes,
                )
                profiles.append(
                    FrozenProfile(
                        information_cutoff=cutoff,
                        window_hours=window_hours,
                        source_minute_publication_sha256=series.manifest.parent_snapshot_sha256,
                        profile_config_sha256=profile_config_sha256,
                        bin_metadata_sha256=bin_metadata_sha256,
                        segment_id=series.segment_id,
                        policy="rolling",
                        bin_definition_id=binning.definition_id,
                        poc_index=snapshot.poc_index,
                        value_area_low_index=snapshot.value_area_low_index,
                        value_area_high_index=snapshot.value_area_high_index,
                        value_area_mid_index=(
                            None
                            if snapshot.value_area_low_index is None
                            else (
                                snapshot.value_area_low_index
                                + cast(int, snapshot.value_area_high_index)
                            )
                            / 2
                        ),
                        profile_content_sha256=content_sha256,
                        active_bin_cells=accumulator.active_bin_count,
                        serialized_bytes=item_bytes,
                        source_start_index=start_index,
                        source_end_index=end_index,
                        source_row_count=window_rows,
                        source_start_timestamp=source_start_timestamp,
                        source_end_timestamp=timestamp,
                        source_first_row_id=first_source_row_id,
                        source_last_row_id=last_source_row_id,
                        source_window_sha256=source_window_sha256,
                        seal=_FROZEN_PROFILE_SEAL,
                    )
                )
                active_bin_cells = next_active_cells
                serialized_bytes = next_serialized_bytes
            source_index += 1
            expected_timestamp += timedelta(minutes=1)
    if source_index != series.manifest.source_row_count:
        raise ValueError("profile parent is missing exact aggregate source rows")
    try:
        next(expected_source_ids)
    except StopIteration:
        pass
    else:
        raise ValueError("aggregate source identities exceed the exact parent row count")
    frozen = tuple(profiles)
    if tuple(profile.information_cutoff for profile in frozen) != expected_cutoffs:
        raise ValueError("verified profile stream contains duplicate or missing rolling cutoffs")
    ordered = tuple(profile.profile_id for profile in frozen)
    source_minute_publication_sha256 = series.manifest.parent_snapshot_sha256
    stream_sha256 = hash_json(
        "verified-candidate-profile-stream-v1",
        {
            "aggregate_series_sha256": series.series_sha256,
            "validation_programme_id": programme.programme_id,
            "work_budget_sha256": budget.sha256,
            "source_minute_publication_sha256": source_minute_publication_sha256,
            "profile_config_sha256": profile_config_sha256,
            "bin_metadata_sha256": bin_metadata_sha256,
            "bin_step": bin_step,
            "bin_origin": bin_origin,
            "bin_definition_id": binning.definition_id,
            "source_price_precision_manifest_sha256": (price_precision_manifest.manifest_sha256),
            "window_hours": window_hours,
            "source_row_count": series.manifest.source_row_count,
            "source_sha256": series.manifest.source_sha256,
            "profile_active_bin_cells": active_bin_cells,
            "profile_source_id_bytes": source_id_bytes,
            "profile_config_bytes": config_bytes,
            "profile_serialized_bytes": serialized_bytes,
            "ordered_profile_ids": ordered,
        },
    )
    return VerifiedProfileStream(
        aggregate_series_sha256=series.series_sha256,
        validation_programme_id=programme.programme_id,
        work_budget_sha256=budget.sha256,
        source_minute_publication_sha256=source_minute_publication_sha256,
        profile_config_sha256=profile_config_sha256,
        bin_metadata_sha256=bin_metadata_sha256,
        bin_step=bin_step,
        bin_origin=bin_origin,
        bin_definition_id=binning.definition_id,
        source_price_precision_manifest_sha256=price_precision_manifest.manifest_sha256,
        window_hours=window_hours,
        source_row_count=series.manifest.source_row_count,
        source_sha256=series.manifest.source_sha256,
        profile_active_bin_cells=active_bin_cells,
        profile_source_id_bytes=source_id_bytes,
        profile_config_bytes=config_bytes,
        profile_serialized_bytes=serialized_bytes,
        profiles=frozen,
        ordered_profile_ids=ordered,
        stream_sha256=stream_sha256,
        seal=_VERIFIED_PROFILE_STREAM_SEAL,
    )


def target_bars(hours: int, timeframe: str) -> int:
    """Convert a civil-hour window to an exact integral target-bar count."""

    if timeframe not in _TIMEFRAME_HOURS:
        raise ValueError("candidate timeframe must be 1h or 4h")
    width = _TIMEFRAME_HOURS[timeframe]
    if isinstance(hours, bool) or not isinstance(hours, int) or hours < 1 or hours % width:
        raise ValueError("candidate hour window must convert to integral target bars")
    return hours // width


def bridge_verified_aggregate_series_v2(
    series: VerifiedAggregateSeriesV2,
) -> VerifiedCandidateSeriesV2:
    """Issue an immutable detector view after revalidating original V2 bytes."""

    if type(series) is not VerifiedAggregateSeriesV2:
        raise TypeError("bridge requires a verifier-issued VerifiedAggregateSeriesV2 capability")
    budget = _candidate_bridge_budget(series)
    verify_verified_aggregate_series_v2(series, budget=budget)
    timeframe_hours = _TIMEFRAME_HOURS.get(series.key.target_timeframe)
    if timeframe_hours is None:
        raise ValueError("candidate detector supports only 1h or 4h aggregate series")
    bars = tuple(
        _CausalAggregateBarV2(
            timestamp=row.timestamp,
            bar_close=row.timestamp + timedelta(hours=timeframe_hours),
            symbol=row.symbol,
            target_timeframe=row.target_timeframe,
            segment_id=row.segment_id,
            source_row_count=row.source_row_count,
            open=float(row.open),
            high=float(row.high),
            low=float(row.low),
            close=float(row.close),
            volume=float(row.volume),
        )
        for row in series.rows
    )
    if any(
        not all(isfinite(value) for value in (bar.open, bar.high, bar.low, bar.close, bar.volume))
        for bar in bars
    ):
        raise ValueError("verified aggregate values exceed the detector numeric domain")
    candidate = VerifiedCandidateSeriesV2(
        bars=bars,
        publication_sha256=series.aggregate_publication_sha256,
        series_sha256=series.series_identity,
        symbol=series.key.symbol,
        target_timeframe=series.key.target_timeframe,
        interval_index=series.key.interval_index,
        segment_id=series.key.segment_id,
        aggregate_schema_version=_AGGREGATE_SCHEMA_V2,
        aggregate_identity=series.aggregate_identity.value,
        aggregate_budget_sha256=series.budget_sha256,
        _factory_token=_VERIFIED_CANDIDATE_SERIES_V2_FACTORY,
    )
    identifier = id(candidate)
    snapshot = _candidate_series_v2_snapshot(candidate)

    def cleanup(reference: weakref.ReferenceType[VerifiedCandidateSeriesV2]) -> None:
        current = _VERIFIED_CANDIDATE_SERIES_V2.get(identifier)
        if current is not None and current.candidate is reference:
            _VERIFIED_CANDIDATE_SERIES_V2.pop(identifier, None)

    _VERIFIED_CANDIDATE_SERIES_V2[identifier] = _VerifiedCandidateSeriesV2Registration(
        candidate=weakref.ref(candidate, cleanup),
        aggregate=series,
        budget=budget,
        snapshot=snapshot,
    )
    return candidate


def verify_candidate_series_v2(series: VerifiedCandidateSeriesV2) -> VerifiedCandidateSeriesV2:
    """Reject substituted, copied, mutated, or stale V2 detector capabilities."""

    if type(series) is not VerifiedCandidateSeriesV2:
        raise TypeError("expected a factory-issued VerifiedCandidateSeriesV2 capability")
    registration = _VERIFIED_CANDIDATE_SERIES_V2.get(id(series))
    if registration is None or registration.candidate() is not series:
        raise ValueError("VerifiedCandidateSeriesV2 is not the registered verifier capability")
    aggregate = registration.aggregate
    if _candidate_series_v2_snapshot(series) != registration.snapshot:
        raise ValueError("VerifiedCandidateSeriesV2 differs from its verifier-issued snapshot")
    verify_verified_aggregate_series_v2(aggregate, budget=registration.budget)
    return series


def _candidate_bridge_budget(
    series: VerifiedAggregateSeriesV2,
) -> AggregatePublicationBudgetV2:
    return AggregatePublicationBudgetV2(
        max_source_rows=1,
        max_source_bytes=1,
        max_parent_partitions=1,
        max_source_rows_per_chunk=240,
        max_members=1,
        max_aggregate_rows=series.row_count,
        max_rows_per_partition=series.row_count,
        max_output_bytes=series.byte_count,
        max_output_files=series.row_count,
    )


def _candidate_series_v2_snapshot(series: VerifiedCandidateSeriesV2) -> tuple[object, ...]:
    return (
        tuple(
            (
                bar.timestamp,
                bar.bar_close,
                bar.symbol,
                bar.target_timeframe,
                bar.segment_id,
                bar.source_row_count,
                bar.open,
                bar.high,
                bar.low,
                bar.close,
                bar.volume,
                bar.schema_version,
                bar.source_timeframe,
                bar.continuity,
                bar.source_row_ids,
                bar.source_sha256,
                bar.parent_snapshot_sha256,
                bar.row_sha256,
            )
            for bar in series.bars
        ),
        series.publication_sha256,
        series.series_sha256,
        series.symbol,
        series.target_timeframe,
        series.interval_index,
        series.segment_id,
        series.aggregate_schema_version,
        series.aggregate_identity,
        series.aggregate_budget_sha256,
    )


def detect_candidate_signals(
    definition: CandidateDefinition,
    series: DetectorSeries,
    *,
    a_opportunities: Sequence[CandidateSignal] = (),
    profile_stream: VerifiedProfileStream | None = None,
) -> tuple[CandidateSignal, ...]:
    """Detect one frozen candidate/comparator over authenticated complete aggregate bars."""

    def detector_signal_issuer() -> Callable[[int, int, int], CandidateSignal]:
        evidence_capability = object()

        @dataclass(frozen=True, slots=True)
        class DetectorEvidence:
            event_index: int
            feature_start_index: int
            cutoff_index: int
            capability: object

        def issue(evidence: DetectorEvidence) -> CandidateSignal:
            if (
                type(evidence) is not DetectorEvidence
                or evidence.capability is not evidence_capability
            ):
                raise TypeError("candidate issuance requires internal detector evidence")
            indices = (
                evidence.event_index,
                evidence.feature_start_index,
                evidence.cutoff_index,
            )
            if any(isinstance(index, bool) or not isinstance(index, int) for index in indices):
                raise ValueError("candidate signal indices must be integers")
            if (
                evidence.feature_start_index < 0
                or evidence.event_index < 0
                or evidence.cutoff_index < 0
                or evidence.feature_start_index > evidence.cutoff_index
                or evidence.event_index > evidence.cutoff_index
                or evidence.cutoff_index >= len(series.bars)
            ):
                raise ValueError(
                    "candidate signal indices are outside the verified causal interval"
                )
            expected_cutoff_index = (
                evidence.event_index + 1 if definition.family == "D" else evidence.event_index
            )
            if evidence.cutoff_index != expected_cutoff_index:
                raise ValueError("candidate signal indices violate the frozen family clock")
            payload: dict[str, object] = {
                "candidate_id": definition.candidate_id,
                "family": definition.family,
                "symbol": series.symbol,
                "timeframe": definition.timeframe,
                "direction": definition.direction,
                "feature_start": series.bars[evidence.feature_start_index].timestamp,
                "information_cutoff": series.bars[evidence.cutoff_index].bar_close,
                "legal_entry": series.bars[evidence.cutoff_index].bar_close,
                "source_publication_sha256": definition.source_publication_sha256,
                "source_series_sha256": definition.source_series_sha256,
                "segment_id": definition.source_segment_id,
                "candidate_slot_id": definition.slot.slot_id,
            }
            token = object()
            _SIGNAL_ISSUANCE_REGISTRY[id(token)] = _SignalIssuanceEntry(
                token=token,
                capability=_CANDIDATE_SIGNAL_ISSUANCE_CAPABILITY,
                payload_sha256=hash_json("candidate-signal-issuance-v1", payload),
            )
            try:
                return CandidateSignal(**payload, issuance_token=token)  # type: ignore[arg-type]
            finally:
                _SIGNAL_ISSUANCE_REGISTRY.pop(id(token), None)

        def record_event(
            event_index: int, feature_start_index: int, cutoff_index: int
        ) -> CandidateSignal:
            evidence = DetectorEvidence(
                event_index=event_index,
                feature_start_index=feature_start_index,
                cutoff_index=cutoff_index,
                capability=evidence_capability,
            )
            return issue(evidence)

        return record_event

    _validate_series(definition, series)
    bars = series.bars
    if not bars:
        return ()
    issuer = detector_signal_issuer()
    if definition.family == "A":
        return _detect_a(definition, series, issuer)
    if definition.family == "B":
        if (
            profile_stream is None
            or profile_stream.stream_sha256 != definition.profile_stream_sha256
        ):
            raise ValueError("family B detection requires its exact verified profile stream")
        return _detect_b(definition, series, a_opportunities, profile_stream, issuer)
    if definition.family == "G":
        return _detect_g(definition, series, issuer)
    if definition.family == "E":
        return _detect_e(definition, series, a_opportunities, issuer)
    return _detect_d(definition, series, issuer)


def _detect_a(
    definition: CandidateDefinition,
    series: DetectorSeries,
    issuer: Callable[[int, int, int], CandidateSignal],
) -> tuple[CandidateSignal, ...]:
    detector = dict(definition.parameters)["detector"]
    if detector == "moving_average_crossover":
        return _detect_a_sma(definition, series, issuer)
    if detector == "donchian_breakout":
        return _detect_a_donchian(definition, series, issuer)
    if detector == "atr_breakout":
        return _detect_a_atr(definition, series, issuer)
    if detector == "time_series_momentum":
        return _detect_a_momentum(definition, series, issuer)
    raise ValueError("unsupported family A detector")


def _detect_a_sma(
    definition: CandidateDefinition,
    series: DetectorSeries,
    issuer: Callable[[int, int, int], CandidateSignal],
) -> tuple[CandidateSignal, ...]:
    bars = series.bars
    fast = _parameter_bars(definition.slot, "fast_hours")
    slow = _parameter_bars(definition.slot, "slow_hours")
    states: list[int | None] = [None] * len(bars)
    for index in range(slow - 1, len(bars)):
        fast_mean = fsum(bar.close for bar in bars[index - fast + 1 : index + 1]) / fast
        slow_mean = fsum(bar.close for bar in bars[index - slow + 1 : index + 1]) / slow
        states[index] = _strict_sign(fast_mean - slow_mean)
    events: list[tuple[int, int, int]] = []
    for index in range(slow, len(bars)):
        state = states[index]
        previous = states[index - 1]
        if state == definition.direction and state != 0 and state != previous:
            events.append((index, index - slow, index))
    return _signals(definition, series, events, issuer)


def _detect_a_donchian(
    definition: CandidateDefinition,
    series: DetectorSeries,
    issuer: Callable[[int, int, int], CandidateSignal],
) -> tuple[CandidateSignal, ...]:
    bars = series.bars
    lookback = _parameter_bars(definition.slot, "lookback_hours")
    armed = True
    events: list[tuple[int, int, int]] = []
    for index in range(lookback, len(bars)):
        upper, lower = _donchian(bars, index, lookback)
        condition = (
            bars[index].close > upper if definition.direction == 1 else bars[index].close < lower
        )
        if condition and armed:
            events.append((index, index - lookback, index))
            armed = False
        elif not armed and lower <= bars[index].close <= upper:
            armed = True
    return _signals(definition, series, events, issuer)


def _detect_a_atr(
    definition: CandidateDefinition,
    series: DetectorSeries,
    issuer: Callable[[int, int, int], CandidateSignal],
) -> tuple[CandidateSignal, ...]:
    bars = series.bars
    count = _parameter_bars(definition.slot, "atr_hours")
    true_ranges = _true_ranges(bars)
    armed = True
    events: list[tuple[int, int, int]] = []
    for index in range(count + 1, len(bars)):
        prior_atr = _mean_true_range(true_ranges[index - count : index])
        if prior_atr is None:
            continue
        threshold = bars[index - 1].close + definition.direction * prior_atr
        condition = (
            bars[index].close > threshold
            if definition.direction == 1
            else bars[index].close < threshold
        )
        if condition and armed:
            events.append((index, index - count - 1, index))
            armed = False
        elif not condition:
            armed = True
    return _signals(definition, series, events, issuer)


def _detect_a_momentum(
    definition: CandidateDefinition,
    series: DetectorSeries,
    issuer: Callable[[int, int, int], CandidateSignal],
) -> tuple[CandidateSignal, ...]:
    bars = series.bars
    lookback = _parameter_bars(definition.slot, "momentum_hours")
    states: list[int | None] = [None] * len(bars)
    for index in range(lookback, len(bars)):
        states[index] = _strict_sign(bars[index].close - bars[index - lookback].close)
    events: list[tuple[int, int, int]] = []
    for index in range(lookback + 1, len(bars)):
        state = states[index]
        if state == definition.direction and state != 0 and state != states[index - 1]:
            events.append((index, index - lookback - 1, index))
    return _signals(definition, series, events, issuer)


def _detect_b(
    definition: CandidateDefinition,
    series: DetectorSeries,
    opportunities: Sequence[CandidateSignal],
    profile_stream: VerifiedProfileStream,
    issuer: Callable[[int, int, int], CandidateSignal],
) -> tuple[CandidateSignal, ...]:
    bars = series.bars
    by_cutoff = {profile.information_cutoff: profile for profile in profile_stream.profiles}
    selected: list[tuple[CandidateSignal, int]] = []
    window_bars = _parameter_bars(definition.slot, "profile_hours")
    window_hours = window_bars * _TIMEFRAME_HOURS[definition.timeframe]
    by_bar_cutoff = {
        bar.bar_close: index for index, bar in enumerate(cast(Sequence[DetectorBar], bars))
    }
    for opportunity in opportunities:
        _validate_opportunity(definition, series, opportunity)
        index = by_bar_cutoff.get(opportunity.information_cutoff)
        if index is None or index < 2:
            continue
        previous = by_cutoff.get(bars[index - 2].bar_close)
        current = by_cutoff.get(bars[index - 1].bar_close)
        if previous is None or current is None:
            continue
        if not _valid_profile(definition, previous, window_hours) or not _valid_profile(
            definition, current, window_hours
        ):
            continue
        if value_migration_acceptance(
            ProfileValueReferences(
                poc_index=previous.poc_index,
                value_area_low_index=previous.value_area_low_index,
                value_area_high_index=previous.value_area_high_index,
                value_area_mid_index=previous.value_area_mid_index,
            ),
            ProfileValueReferences(
                poc_index=current.poc_index,
                value_area_low_index=current.value_area_low_index,
                value_area_high_index=current.value_area_high_index,
                value_area_mid_index=current.value_area_mid_index,
            ),
            prior_close_bin=profile_stream.bin_index(bars[index - 1].close),
            current_close_bin=profile_stream.bin_index(bars[index].close),
            direction=definition.direction,
        ):
            selected.append((opportunity, index))
    role = definition.role
    all_opportunities = [
        (opportunity, by_bar_cutoff[opportunity.information_cutoff])
        for opportunity in opportunities
        if opportunity.information_cutoff in by_bar_cutoff
    ]
    if role == "price_baseline":
        chosen = all_opportunities
    elif role in ("structure_only", "combined_primary"):
        chosen = selected
    elif role == "rate_matched_placebo":
        chosen = _rate_matched(definition, all_opportunities, len(selected))
    else:
        raise ValueError("unsupported family B role")
    signals = tuple(
        _signal_from_opportunity(
            definition,
            series,
            opportunity,
            cutoff_index=index,
            feature_start_index=(
                _timestamp_index(series, opportunity.feature_start)
                if role == "price_baseline"
                else min(
                    _timestamp_index(series, opportunity.feature_start),
                    _timestamp_index(series, by_cutoff[bars[index - 2].bar_close].feature_start),
                )
            ),
            issuer=issuer,
        )
        for opportunity, index in chosen
    )
    return _suppress_overlap(definition, signals)


def _detect_e(
    definition: CandidateDefinition,
    series: DetectorSeries,
    opportunities: Sequence[CandidateSignal],
    issuer: Callable[[int, int, int], CandidateSignal],
) -> tuple[CandidateSignal, ...]:
    bars = series.bars
    count = _parameter_bars(definition.slot, "volume_median_hours")
    by_cutoff = {
        bar.bar_close: index for index, bar in enumerate(cast(Sequence[DetectorBar], bars))
    }
    eligible: list[tuple[CandidateSignal, int]] = []
    selected: list[tuple[CandidateSignal, int]] = []
    for opportunity in opportunities:
        _validate_opportunity(definition, series, opportunity)
        index = by_cutoff.get(opportunity.information_cutoff)
        if index is None or index < count:
            continue
        item = (opportunity, index)
        eligible.append(item)
        if bars[index].volume > median(bar.volume for bar in bars[index - count : index]):
            selected.append(item)
    if definition.role == "price_only":
        chosen = eligible
    elif definition.role == "volume_filtered_primary":
        chosen = selected
    elif definition.role == "rate_matched_placebo":
        chosen = _rate_matched(definition, eligible, len(selected))
    else:
        raise ValueError("unsupported family E role")
    signals = tuple(
        _signal_from_opportunity(
            definition,
            series,
            opportunity,
            cutoff_index=index,
            feature_start_index=min(
                _timestamp_index(series, opportunity.feature_start), index - count
            ),
            issuer=issuer,
        )
        for opportunity, index in chosen
    )
    return _suppress_overlap(definition, signals)


def _detect_g(
    definition: CandidateDefinition,
    series: DetectorSeries,
    issuer: Callable[[int, int, int], CandidateSignal],
) -> tuple[CandidateSignal, ...]:
    bars = series.bars
    n8 = _parameter_bars(definition.slot, "atr_short_hours")
    n24 = _parameter_bars(definition.slot, "atr_long_hours")
    donchian = _parameter_bars(definition.slot, "donchian_hours")
    true_ranges = _true_ranges(bars)
    armed = True
    events: list[tuple[int, int, int]] = []
    first = {
        "candidate_primary": max(n24 + 3, donchian),
        "atr_only_control": n24 + 1,
        "donchian_only_control": donchian,
    }.get(definition.role)
    if first is None:
        raise ValueError("unsupported family G role")
    for index in range(first, len(bars)):
        upper, lower = _donchian(bars, index, donchian)
        breakout = (
            bars[index].close > upper if definition.direction == 1 else bars[index].close < lower
        )
        prior_atr = _mean_true_range(true_ranges[index - n24 : index])
        current_true_range = true_ranges[index]
        expansion = (
            prior_atr is not None
            and current_true_range is not None
            and current_true_range > prior_atr
        )
        if definition.role == "candidate_primary":
            compression = all(
                (short := _mean_true_range(true_ranges[j - n8 + 1 : j + 1])) is not None
                and (long := _mean_true_range(true_ranges[j - n24 + 1 : j + 1])) is not None
                and short < long
                for j in (index - 3, index - 2, index - 1)
            )
            condition = compression and expansion and breakout
        elif definition.role == "atr_only_control":
            condition = expansion and _strict_sign(bars[index].close - bars[index - 1].close) == (
                definition.direction
            )
        elif definition.role == "donchian_only_control":
            condition = breakout
        else:
            condition = breakout
        emit = condition and (armed if definition.role == "candidate_primary" else True)
        if emit:
            feature_start = {
                "candidate_primary": index - n24 - 3,
                "atr_only_control": index - n24 - 1,
                "donchian_only_control": index - donchian,
            }[definition.role]
            events.append((index, feature_start, index))
            if definition.role == "candidate_primary":
                armed = False
        elif definition.role == "candidate_primary" and not condition:
            armed = True
    return _signals(definition, series, events, issuer)


def _detect_d(
    definition: CandidateDefinition,
    series: DetectorSeries,
    issuer: Callable[[int, int, int], CandidateSignal],
) -> tuple[CandidateSignal, ...]:
    bars = series.bars
    lookback = _parameter_bars(definition.slot, "level_hours")
    week = target_bars(24 * 7, definition.timeframe)
    pseudo = definition.role == "pseudo_level_control"
    first = lookback + week if pseudo else lookback
    armed = True
    events: list[tuple[int, int, int]] = []
    for index in range(first, len(bars) - 1):
        reference = index - week if pseudo else index
        upper, lower = _donchian(bars, reference, lookback)
        if definition.direction == -1:
            breach = bars[index].high > upper and bars[index].close <= upper
            confirmed = bars[index + 1].close < upper
        else:
            breach = bars[index].low < lower and bars[index].close >= lower
            confirmed = bars[index + 1].close > lower
        condition = breach and (confirmed or definition.role == "failed_donchian_control")
        if condition and armed:
            events.append((index, reference - lookback, index + 1))
            armed = False
        elif not breach:
            armed = True
    return _signals(definition, series, events, issuer)


def _signals(
    definition: CandidateDefinition,
    series: DetectorSeries,
    events: Sequence[tuple[int, int, int]],
    issuer: Callable[[int, int, int], CandidateSignal],
) -> tuple[CandidateSignal, ...]:
    bars = series.bars
    output: list[CandidateSignal] = []
    frozen_until: datetime | None = None
    for signal_index, start_index, cutoff_index in events:
        cutoff = bars[cutoff_index].bar_close
        legal_entry = cutoff
        if frozen_until is not None and legal_entry < frozen_until:
            continue
        output.append(issuer(signal_index, start_index, cutoff_index))
        frozen_until = legal_entry + timedelta(hours=definition.horizon_hours)
    return tuple(output)


def _signal_from_opportunity(
    definition: CandidateDefinition,
    series: DetectorSeries,
    opportunity: CandidateSignal,
    *,
    feature_start_index: int,
    cutoff_index: int,
    issuer: Callable[[int, int, int], CandidateSignal],
) -> CandidateSignal:
    if (
        opportunity.information_cutoff != series.bars[cutoff_index].bar_close
        or opportunity.legal_entry != opportunity.information_cutoff
    ):
        raise ValueError("subordinate signal bar does not match its verified cutoff index")
    return issuer(cutoff_index, feature_start_index, cutoff_index)


def _timestamp_index(series: DetectorSeries, timestamp: datetime) -> int:
    first = series.bars[0]
    width = first.bar_close - first.timestamp
    offset = timestamp - first.timestamp
    index = offset // width
    if (
        offset < timedelta(0)
        or offset % width != timedelta(0)
        or index >= len(series.bars)
        or series.bars[index].timestamp != timestamp
    ):
        raise ValueError("subordinate feature start is outside the verified aggregate series")
    return index


def _suppress_overlap(
    definition: CandidateDefinition, signals: Sequence[CandidateSignal]
) -> tuple[CandidateSignal, ...]:
    output: list[CandidateSignal] = []
    frozen_until: datetime | None = None
    for signal in sorted(signals, key=lambda item: (item.legal_entry, item.signal_id)):
        if frozen_until is not None and signal.legal_entry < frozen_until:
            continue
        output.append(signal)
        frozen_until = signal.legal_entry + timedelta(hours=definition.horizon_hours)
    return tuple(output)


def _rate_matched(
    definition: CandidateDefinition,
    population: Sequence[tuple[CandidateSignal, int]],
    count: int,
) -> list[tuple[CandidateSignal, int]]:
    ranked = sorted(
        population,
        key=lambda item: hash_json(
            "outcome-blind-rate-matched-placebo-v1",
            {"candidate_id": definition.candidate_id, "opportunity_id": item[0].signal_id},
        ),
    )
    return ranked[:count]


def _valid_profile(
    definition: CandidateDefinition, profile: FrozenProfile, window_hours: int
) -> bool:
    return not (
        profile.window_hours != window_hours
        or profile.segment_id != definition.source_segment_id
        or not profile.bin_definition_id
    )


def _validate_opportunity(
    definition: CandidateDefinition,
    series: DetectorSeries,
    signal: CandidateSignal,
) -> None:
    if signal.family != "A":
        raise ValueError("subordinate candidates require family A opportunities")
    if (
        signal.candidate_id != definition.parent_a_candidate_id
        or signal.candidate_slot_id != definition.parent_a_slot_id
        or signal.source_series_sha256 != series.series_sha256
        or signal.source_publication_sha256 != definition.source_publication_sha256
        or signal.segment_id != definition.source_segment_id
        or signal.timeframe != definition.timeframe
        or signal.direction != definition.direction
    ):
        raise ValueError("A opportunity is not the exact registered parent of this candidate")


def _validate_series(definition: CandidateDefinition, series: DetectorSeries) -> None:
    if type(series) is VerifiedCandidateSeriesV2:
        verify_candidate_series_v2(series)
        expected_config_version = (
            f"{series.aggregate_schema_version}:{series.aggregate_identity}:"
            f"{series.aggregate_budget_sha256}"
        )
        if definition.aggregate_config_version != expected_config_version:
            raise ValueError(
                "candidate definition does not own this aggregate schema/config identity"
            )
    elif type(series) is not VerifiedAggregateSeries:
        raise TypeError(
            "candidate detection requires an exact VerifiedAggregateSeries or "
            "VerifiedCandidateSeriesV2 capability"
        )
    if (
        definition.source_series_sha256 != series.series_sha256
        or definition.source_publication_sha256 != series.publication_sha256
        or definition.source_segment_id != series.segment_id
        or definition.timeframe != series.target_timeframe
    ):
        raise ValueError("candidate definition does not own this verified aggregate series")


def _profile_payload(snapshot: ProfileSnapshot) -> dict[str, object]:
    return {
        "bin_volumes": [[index, volume] for index, volume in sorted(snapshot.bin_volumes.items())],
        "total_volume": snapshot.total_volume,
        "poc_index": snapshot.poc_index,
        "value_area_low_index": snapshot.value_area_low_index,
        "value_area_high_index": snapshot.value_area_high_index,
        "vwap": snapshot.vwap,
        "binning_id": snapshot.binning_id,
        "allocation_id": snapshot.allocation_id,
        "value_area_fraction": snapshot.value_area_fraction,
        "work_budget_id": snapshot.work_budget_id,
    }


def _compact_profile_payload(
    *,
    information_cutoff: datetime,
    window_hours: int,
    source_minute_publication_sha256: str,
    profile_config_sha256: str,
    bin_metadata_sha256: str,
    segment_id: int,
    bin_definition_id: str,
    source_start_index: int,
    source_end_index: int,
    source_row_count: int,
    source_start_timestamp: datetime,
    source_end_timestamp: datetime,
    source_first_row_id: str,
    source_last_row_id: str,
    source_window_sha256: str,
    profile_content_sha256: str,
    snapshot: ProfileSnapshot,
) -> dict[str, object]:
    """Return the exact compact B-detector record without retaining its bin-volume map."""

    return {
        "information_cutoff": information_cutoff.isoformat(),
        "window_hours": window_hours,
        "source_minute_publication_sha256": source_minute_publication_sha256,
        "profile_config_sha256": profile_config_sha256,
        "bin_metadata_sha256": bin_metadata_sha256,
        "segment_id": segment_id,
        "policy": "rolling",
        "source_timeframe": "1m",
        "bin_definition_id": bin_definition_id,
        "source_start_index": source_start_index,
        "source_end_index": source_end_index,
        "source_row_count": source_row_count,
        "source_start_timestamp": source_start_timestamp.isoformat(),
        "source_end_timestamp": source_end_timestamp.isoformat(),
        "source_first_row_id": source_first_row_id,
        "source_last_row_id": source_last_row_id,
        "source_window_sha256": source_window_sha256,
        "profile_content_sha256": profile_content_sha256,
        "active_bin_cells": len(snapshot.bin_volumes),
        "poc_index": snapshot.poc_index,
        "value_area_low_index": snapshot.value_area_low_index,
        "value_area_high_index": snapshot.value_area_high_index,
        "value_area_mid_index": (
            None
            if snapshot.value_area_low_index is None
            else (snapshot.value_area_low_index + cast(int, snapshot.value_area_high_index)) / 2
        ),
    }


def _require_profile_budget(stage: str, observed: int, limit: int) -> None:
    if observed > limit:
        raise ValidationWorkBudgetViolation(stage, observed, limit)


def _artifact_bytes(payload: Mapping[str, object]) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _parse_artifact(content: bytes, label: str) -> dict[str, object]:
    if not isinstance(content, bytes):
        raise TypeError(f"{label} artifact must be supplied as bytes")
    try:
        payload = json.loads(content.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError(f"{label} artifact is invalid JSON") from error
    if not isinstance(payload, dict) or any(not isinstance(key, str) for key in payload):
        raise ValueError(f"{label} artifact must contain one JSON object")
    return cast(dict[str, object], payload)


def _profile_window_sha256(
    *,
    source_sha256: str,
    start_index: int,
    end_index: int,
    first_source_row_id: str,
    last_source_row_id: str,
) -> str:
    return hash_json(
        "candidate-profile-source-window-v1",
        {
            "source_sha256": source_sha256,
            "start_index": start_index,
            "end_index": end_index,
            "row_count": end_index - start_index,
            "first_source_row_id": first_source_row_id,
            "last_source_row_id": last_source_row_id,
        },
    )


def _parameter_bars(slot: ValidationSlot, name: str) -> int:
    parameters = dict(slot.parameters)
    base = target_bars(int(parameters[name]), slot.timeframe)
    if (
        slot.kind is ValidationSlotKind.PERTURBATION
        and parameters.get("perturbed_parameter") == name
    ):
        return int(parameters["candidate_bars"])
    return base


def _frozen_profile_window_hours() -> tuple[int, ...]:
    return tuple(
        sorted(
            {
                _parameter_bars(slot, "profile_hours") * _TIMEFRAME_HOURS[slot.timeframe]
                for slot in VALIDATION_SLOT_ROSTER
                if slot.family == "B"
                and slot.kind in (ValidationSlotKind.CORE, ValidationSlotKind.PERTURBATION)
            }
        )
    )


def _true_ranges(bars: Sequence[DetectorBar]) -> tuple[float | None, ...]:
    output: list[float | None] = []
    for index, bar in enumerate(bars):
        if not index:
            output.append(None)
            continue
        output.append(
            max(
                bar.high - bar.low,
                abs(bar.high - bars[index - 1].close),
                abs(bar.low - bars[index - 1].close),
            )
        )
    return tuple(output)


def _mean_true_range(values: Sequence[float | None]) -> float | None:
    if not values or any(value is None for value in values):
        return None
    return fsum(cast(float, value) for value in values) / len(values)


def _donchian(
    bars: Sequence[DetectorBar], index: int, lookback: int
) -> tuple[float, float]:
    reference = bars[index - lookback : index]
    return max(bar.high for bar in reference), min(bar.low for bar in reference)


def _strict_sign(value: float) -> int:
    return 1 if value > 0 else -1 if value < 0 else 0


def _require_sha256(value: object, label: str) -> None:
    if (
        not isinstance(value, str)
        or len(value) != _SHA256_LENGTH
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise ValueError(f"{label} must be a lower-case SHA-256")


__all__ = [
    "A_SELECTOR_GRID",
    "CandidateSignal",
    "FrozenProfile",
    "ProfileValueReferences",
    "VerifiedCandidateSeriesV2",
    "VerifiedProfileStream",
    "bridge_verified_aggregate_series_v2",
    "build_verified_profile_stream",
    "candidate_definition_for_slot",
    "detect_candidate_signals",
    "profile_config_artifact_bytes",
    "target_bars",
    "value_migration_acceptance",
    "verify_candidate_series_v2",
]
