"""Audited Phase 3 discovery feature definitions."""

from __future__ import annotations

from market_structure_lab.auction.engine import AuctionLocation
from market_structure_lab.features.registry import (
    BUILTIN_FEATURE_BUILDER_ID,
    BUILTIN_FEATURE_BUILDER_VERSION,
    FeatureDefinition,
    FeatureFamily,
    FeatureRegistry,
    FeatureValueKind,
    LeakageClass,
    MissingPolicy,
    NormalizationRequirement,
    ObservableCutoffRule,
)

BUILTIN_FEATURE_SET_ID = "FS-000001"


def _definition(
    name: str,
    formula: str,
    family: FeatureFamily,
    units: str,
    required_prior: int,
    *,
    source_fields: tuple[str, ...],
    trailing_window: str | None = None,
    value_kind: FeatureValueKind = FeatureValueKind.FLOAT,
    allowed_categories: tuple[str, ...] = (),
) -> FeatureDefinition:
    continuity_fields = (
        (
            "snapshot.events.kind",
            "snapshot.segment_id",
            "snapshot.timestamp",
        )
        if required_prior > 0 or trailing_window == "since_continuity_boundary"
        else ()
    )
    return FeatureDefinition(
        name=name,
        definition=formula,
        family=family,
        value_kind=value_kind,
        units=units,
        required_prior_observations=required_prior,
        missing_policy=MissingPolicy.NULL,
        version="v1",
        leakage_class=(
            LeakageClass.AT_CUTOFF if required_prior == 0 else LeakageClass.TRAILING_ONLY
        ),
        source_fields=tuple(sorted({*source_fields, *continuity_fields})),
        trailing_window=(
            trailing_window
            or (
                "current_observation"
                if required_prior == 0
                else f"trailing_{required_prior + 1}_observations"
            )
        ),
        observable_cutoff_rule=(
            ObservableCutoffRule.AT_INFORMATION_CUTOFF
            if required_prior == 0 and trailing_window != "since_continuity_boundary"
            else ObservableCutoffRule.TRAILING_THROUGH_INFORMATION_CUTOFF
        ),
        normalization_requirement=(
            NormalizationRequirement.NOT_REQUIRED
            if value_kind is FeatureValueKind.CATEGORY
            else NormalizationRequirement.TRAINING_PARTITION_FITTED
        ),
        future_outcome_prohibited=True,
        builder_id=BUILTIN_FEATURE_BUILDER_ID,
        builder_version=BUILTIN_FEATURE_BUILDER_VERSION,
        allowed_categories=allowed_categories,
    )


BUILTIN_DEFINITIONS = (
    _definition(
        "auction_location",
        "Current AuctionSnapshot.location.value category.",
        FeatureFamily.AUCTION,
        "category",
        0,
        source_fields=("snapshot.location",),
        value_kind=FeatureValueKind.CATEGORY,
        allowed_categories=tuple(location.value for location in AuctionLocation),
    ),
    _definition(
        "poc_distance_close",
        "(current close - current POC price) / current close.",
        FeatureFamily.AUCTION,
        "fraction_of_close",
        0,
        source_fields=("snapshot.latest_candle.close", "snapshot.profile.point_of_control"),
    ),
    _definition(
        "poc_velocity_close_1",
        "(current POC price - previous POC price) / previous close.",
        FeatureFamily.AUCTION,
        "fraction_of_previous_close",
        1,
        source_fields=(
            "snapshot.latest_candle.close",
            "snapshot.profile.point_of_control",
        ),
    ),
    _definition(
        "value_width_close",
        "(current VAH price - current VAL price) / current close.",
        FeatureFamily.AUCTION,
        "fraction_of_close",
        0,
        source_fields=(
            "snapshot.latest_candle.close",
            "snapshot.profile.value_area_high",
            "snapshot.profile.value_area_low",
        ),
    ),
    _definition(
        "value_midpoint_velocity_close_1",
        "(current value-area midpoint - previous value-area midpoint) / previous close.",
        FeatureFamily.AUCTION,
        "fraction_of_previous_close",
        1,
        source_fields=(
            "snapshot.latest_candle.close",
            "snapshot.profile.value_area_high",
            "snapshot.profile.value_area_low",
        ),
    ),
    _definition(
        "poc_volume_share",
        "Current POC-bin volume / current profile total volume.",
        FeatureFamily.AUCTION,
        "fraction",
        0,
        source_fields=(
            "snapshot.profile.bin_volumes",
            "snapshot.profile.poc_index",
            "snapshot.profile.total_volume",
        ),
    ),
    _definition(
        "vwap_distance_close",
        "(current close - current VWAP) / current close.",
        FeatureFamily.AUCTION,
        "fraction_of_close",
        0,
        source_fields=("snapshot.latest_candle.close", "snapshot.profile.vwap"),
    ),
    _definition(
        "vwap_slope_close_1",
        "(current VWAP - previous VWAP) / previous close.",
        FeatureFamily.AUCTION,
        "fraction_of_previous_close",
        1,
        source_fields=("snapshot.latest_candle.close", "snapshot.profile.vwap"),
    ),
    _definition(
        "close_value_position",
        "(current close - current VAL price) / (current VAH price - current VAL price).",
        FeatureFamily.AUCTION,
        "fraction",
        0,
        source_fields=(
            "snapshot.latest_candle.close",
            "snapshot.profile.value_area_high",
            "snapshot.profile.value_area_low",
        ),
    ),
    _definition(
        "value_area_jaccard_1",
        "Cardinality of the intersection divided by the union of previous and current inclusive integer value-area bin ranges.",
        FeatureFamily.AUCTION,
        "fraction",
        1,
        source_fields=(
            "snapshot.profile.value_area_high_index",
            "snapshot.profile.value_area_low_index",
        ),
    ),
    _definition(
        "nearest_hvn_distance_close",
        "Signed (current close - nearest current HVN representative price) / current close; nearest minimizes absolute price distance.",
        FeatureFamily.AUCTION,
        "fraction_of_close",
        0,
        source_fields=(
            "snapshot.latest_candle.close",
            "snapshot.nodes.kind",
            "snapshot.nodes.price",
            "snapshot.nodes.representative_index",
        ),
    ),
    _definition(
        "nearest_lvn_distance_close",
        "Signed (current close - nearest current LVN representative price) / current close; nearest minimizes absolute price distance.",
        FeatureFamily.AUCTION,
        "fraction_of_close",
        0,
        source_fields=(
            "snapshot.latest_candle.close",
            "snapshot.nodes.kind",
            "snapshot.nodes.price",
            "snapshot.nodes.representative_index",
        ),
    ),
    _definition(
        "max_node_persistence_bars",
        "Maximum current profile-node persistence in bars, or zero when no nodes exist.",
        FeatureFamily.AUCTION,
        "bars",
        0,
        source_fields=("snapshot.nodes.persistence",),
        value_kind=FeatureValueKind.INTEGER,
    ),
    _definition(
        "inside_value_rate_20",
        "Mean over current plus 19 trailing snapshots of location in lower_value, point_of_control, or upper_value; null unless all 20 locations are not no_value.",
        FeatureFamily.AUCTION,
        "fraction",
        19,
        source_fields=("snapshot.location",),
    ),
    _definition(
        "value_reentry_rate_20",
        "Count of VALUE_REENTRY events over current plus 19 trailing snapshots divided by 20.",
        FeatureFamily.AUCTION,
        "events_per_bar",
        19,
        source_fields=("snapshot.events.kind",),
    ),
    _definition(
        "location_dwell_bars",
        "Consecutive observations in the current auction location, reset at every continuity boundary.",
        FeatureFamily.AUCTION,
        "bars",
        0,
        source_fields=("snapshot.location",),
        trailing_window="since_continuity_boundary",
        value_kind=FeatureValueKind.INTEGER,
    ),
    _definition(
        "log_return_1",
        "Natural logarithm of current close / previous close; null when either close is non-positive.",
        FeatureFamily.SEQUENCE,
        "log_return",
        1,
        source_fields=("snapshot.latest_candle.close",),
    ),
    _definition(
        "range_close_fraction",
        "(current high - current low) / current close.",
        FeatureFamily.SEQUENCE,
        "fraction_of_close",
        0,
        source_fields=(
            "snapshot.latest_candle.close",
            "snapshot.latest_candle.high",
            "snapshot.latest_candle.low",
        ),
    ),
    _definition(
        "body_range_ratio",
        "Signed (current close - current open) / (current high - current low).",
        FeatureFamily.SEQUENCE,
        "fraction_of_range",
        0,
        source_fields=(
            "snapshot.latest_candle.close",
            "snapshot.latest_candle.high",
            "snapshot.latest_candle.low",
            "snapshot.latest_candle.open",
        ),
    ),
    _definition(
        "upper_wick_range_ratio",
        "(current high - max(current open, current close)) / (current high - current low).",
        FeatureFamily.SEQUENCE,
        "fraction_of_range",
        0,
        source_fields=(
            "snapshot.latest_candle.close",
            "snapshot.latest_candle.high",
            "snapshot.latest_candle.low",
            "snapshot.latest_candle.open",
        ),
    ),
    _definition(
        "lower_wick_range_ratio",
        "(min(current open, current close) - current low) / (current high - current low).",
        FeatureFamily.SEQUENCE,
        "fraction_of_range",
        0,
        source_fields=(
            "snapshot.latest_candle.close",
            "snapshot.latest_candle.high",
            "snapshot.latest_candle.low",
            "snapshot.latest_candle.open",
        ),
    ),
    _definition(
        "log_volume_ratio_1",
        "Natural logarithm of current volume / previous volume; null when either volume is non-positive.",
        FeatureFamily.SEQUENCE,
        "log_ratio",
        1,
        source_fields=("snapshot.latest_candle.volume",),
    ),
    _definition(
        "realized_volatility_20",
        "Square root of the mean squared log return over the 20 trailing one-observation returns.",
        FeatureFamily.SEQUENCE,
        "log_return",
        20,
        source_fields=("snapshot.latest_candle.close",),
    ),
    _definition(
        "volatility_normalized_return_20",
        "Current log return divided by realized_volatility_20; null when volatility is zero.",
        FeatureFamily.SEQUENCE,
        "ratio",
        20,
        source_fields=("snapshot.latest_candle.close",),
    ),
    _definition(
        "return_autocorrelation_1_20",
        "Pearson correlation of the 19 lag-one pairs within the 20 trailing log returns; null when either side has zero variance.",
        FeatureFamily.SEQUENCE,
        "correlation",
        20,
        source_fields=("snapshot.latest_candle.close",),
    ),
    _definition(
        "volume_relative_median_20",
        "Current volume / median(current plus 19 trailing volumes) - 1; null when the median is zero.",
        FeatureFamily.SEQUENCE,
        "fraction",
        19,
        source_fields=("snapshot.latest_candle.volume",),
    ),
)


def builtin_feature_registry() -> FeatureRegistry:
    """Return the immutable audited Phase 3 discovery registry."""

    return FeatureRegistry(BUILTIN_FEATURE_SET_ID, BUILTIN_DEFINITIONS)
