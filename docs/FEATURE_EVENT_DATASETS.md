# Feature and Event Datasets

Phase 3 converts immutable auction snapshots into versioned, outcome-blind discovery inputs. This
layer does not attach forward returns, labels, trade outcomes, profitability, MFE/MAE, or any other
future information. A feature vector is evidence, not a behaviour, hypothesis, edge, or strategy.

## Time and identity

Canonical candle timestamps are candle-open timestamps. Every feature row has an
`information_cutoff` equal to the exclusive candle close:

```text
information_cutoff = timestamp + timeframe_duration(timeframe)
```

Rows are immutable and retain dataset, auction configuration, profile definition, window policy,
feature-set, registry, symbol, timeframe, and canonical segment identities. A naive or non-UTC
timestamp, a mismatched cutoff, an undeclared column, or a non-finite value fails closed.

The initial audited discovery registry is `FS-000001`. Its canonical metadata hash determines the
`FR-...` registry ID. Changing a definition, unit, family, required history, missing policy,
version, category, or leakage class changes that registry identity; formula changes must allocate a
new feature-set identity rather than silently altering published data.

## Causal feature construction

`market_structure_lab.features.FeatureBuilder` consumes one ordered `AuctionSnapshot` stream and
emits one row per snapshot. It holds at most 21 snapshots of trailing state. Symbol or timeframe
changes, identity drift in dataset/configuration/profile/window metadata, duplicate timestamps,
out-of-order timestamps, and unexplained gaps are rejected. Segment, gap, and window reset events
clear all rolling history before the current row. No value is interpolated, forward-filled, or
replaced with an arbitrary epsilon.

The auction-informed family is:

| Feature | Definition | Prior observations |
|---|---|---:|
| `auction_location` | Current deterministic auction-location category | 0 |
| `poc_distance_close` | `(close - POC) / close` | 0 |
| `poc_velocity_close_1` | `(POC[t] - POC[t-1]) / close[t-1]` | 1 |
| `value_width_close` | `(VAH - VAL) / close` | 0 |
| `value_midpoint_velocity_close_1` | Change in value midpoint divided by prior close | 1 |
| `poc_volume_share` | POC-bin volume divided by total profile volume | 0 |
| `vwap_distance_close` | `(close - VWAP) / close` | 0 |
| `vwap_slope_close_1` | `(VWAP[t] - VWAP[t-1]) / close[t-1]` | 1 |
| `close_value_position` | `(close - VAL) / (VAH - VAL)` | 0 |
| `value_area_jaccard_1` | Intersection/union cardinality of consecutive inclusive integer value-area ranges | 1 |
| `nearest_hvn_distance_close` | Signed distance from close to the nearest current HVN representative price, divided by close | 0 |
| `nearest_lvn_distance_close` | Signed distance from close to the nearest current LVN representative price, divided by close | 0 |
| `max_node_persistence_bars` | Greatest current node persistence, or zero with no nodes | 0 |
| `inside_value_rate_20` | Fraction of 20 locations inside lower value, POC, or upper value | 19 |
| `value_reentry_rate_20` | Value re-entry event count over 20 snapshots divided by 20 | 19 |
| `location_dwell_bars` | Consecutive observations in the current location within one continuity segment | 0 |

The minimally assumptive sequence family is:

| Feature | Definition | Prior observations |
|---|---|---:|
| `log_return_1` | `ln(close[t] / close[t-1])` | 1 |
| `range_close_fraction` | `(high - low) / close` | 0 |
| `body_range_ratio` | Signed candle body divided by high-low range | 0 |
| `upper_wick_range_ratio` | Upper wick divided by high-low range | 0 |
| `lower_wick_range_ratio` | Lower wick divided by high-low range | 0 |
| `log_volume_ratio_1` | `ln(volume[t] / volume[t-1])` | 1 |
| `realized_volatility_20` | Root mean square of 20 trailing one-bar log returns | 20 |
| `volatility_normalized_return_20` | Current log return divided by trailing realized volatility | 20 |
| `return_autocorrelation_1_20` | Pearson lag-one correlation across the 19 pairs in 20 trailing returns | 20 |
| `volume_relative_median_20` | Current volume divided by the 20-observation median, minus one | 19 |

Required-history fields remain null until the exact first eligible observation. A zero divisor,
zero range, non-positive input to a logarithm, empty profile reference, unavailable node, or
zero-variance correlation also produces an explicit null. `inside_value_rate_20` is null if any
member has `no_value`; a zero-volume profile does not fabricate a POC share. These missing values
are part of the registered representation and must not be silently filled downstream.

Auction features consume the Phase 2 snapshot and do not recalculate POC, value area, VWAP, node
zones, persistence, or structural events. Integer bin references remain canonical internally;
normalized price distances are produced only at the public feature boundary.

## Robust normalization

`fit_robust_normalizer` requires a caller-declared, half-open UTC training cutoff partition. It
selects registered numeric features only and calculates deterministic median and interquartile
range parameters from rows wholly inside that training partition. Validation and holdout roles are
rejected for fitting.

Fitting is exact and bounded-memory. Values are sorted into deterministic binary runs capped by
`max_rows_per_run`, written below the caller-selected temporary directory, and merged in bounded
passes capped by `max_buffered_rows`. The artifact records both limits. Temporary runs are removed
after success or failure; no quantile approximation or full-column materialisation is used.

Transform is immutable and never refits. Nulls remain null. A zero-IQR feature uses a documented
scale of `1.0`, so it remains centered without division by zero. The serialized artifact pins the
dataset snapshot, feature set, registry, training split and bounds, row count, selected features,
algorithm version, medians, IQRs, scales, and a canonical SHA-256. Identity drift and artifact
tampering fail closed.

## Event semantics

Discovery events use half-open intervals `[start, end)`. Their information cutoff is exactly
`end`, and the attached feature vector is the registered row observable at that cutoff. Stable event
IDs include dataset, auction configuration, profile, window, feature-set, registry, symbol,
timeframe, segment, event kind, interval bounds, cutoff, trigger version, and feature-vector hash.
Event construction and every detector require an explicit `FeatureRegistry`; undeclared,
outcome-classified, or otherwise discovery-unsafe feature vectors fail closed. Event metadata
describes only contemporaneous trigger evidence.

Supported segmentation includes fixed non-overlapping windows, explicitly exploratory rolling
windows, UTC sessions, causal change points, volatility/volume expansion, value-area exits and
re-entries, POC migration, and node tests/traversals. No event may cross a canonical segment or
continuity reset. Change-point and expansion baselines use completed prior history only. Structural
value and POC events preserve the Phase 2 observable trigger. Node interactions use an already
observed prior snapshot node zone, never a zone inferred from future candles.

Structural event metadata distinguishes `source_candle_open_timestamp` from
`observable_trigger_timestamp`. The latter is the source candle's exclusive close and is never
reported as though the candle-open timestamp were already observable.

Overlap is not hidden. Reports record total and by-kind counts, overlapping interval pairs,
affected-event ratio, maximum concurrency, and by-kind-pair counts. Rolling events remain marked
exploratory so their dependent overlapping samples cannot be mistaken for independent evidence.
Overlap accounting consumes canonical ordered events, rejects unordered input, and retains only
the active concurrent intervals rather than sorting or materialising the full event collection.

## Publication contract

Feature and event datasets are written in bounded row chunks to deterministic symbol/year/month
Parquet partitions under a dataset-version and feature-set root. A canonical manifest pins the
source dataset snapshot, registry, auction/event/normalizer/configuration versions, code identity,
lockfile identity, schemas, row and missing-value evidence, event/overlap evidence, and every part
checksum and time bound.

Publication uses staging and writes `_SUCCESS` only after the manifest and all partitions verify.
Repeating an identical publication verifies and returns the existing result. Reusing an existing
identity with different content, an unmanifested part, a checksum mismatch, duplicate/out-of-order
rows, or a stale conflicting stage fails closed. An empty input publishes an explicit zero-row
manifest and never fabricates a row.

Import publication APIs from `market_structure_lab.data.derived`. They are deliberately not
re-exported by `market_structure_lab.data`, which keeps canonical data models independent from the
feature layer and prevents import-order-dependent package cycles in fresh installations.
