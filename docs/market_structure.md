# Market Structure

Reference notes for market-structure concepts, data definitions, and modeling assumptions.

## Core auction concepts

- Value: the price region where the auction has accepted trade.
- Point of Control: the highest-volume price level in a profile window.
- Value Area: the price range containing the selected share of traded volume.
- HVN: high-volume node, interpreted as accepted value.
- LVN: low-volume node, interpreted as rejected or inefficient value.
- Balance: bounded auction behavior around accepted value.
- Imbalance: directional movement away from a prior value area.
- Acceptance: time and volume sustained beyond a prior reference.
- Rejection: failed movement beyond a reference followed by return into value.

The Phase 2 representation implements POC, value area, VWAP, local node zones, value migration,
location, and structural boundary events deterministically. Broader acceptance, rejection, balance,
and imbalance definitions remain later work and must not be inferred from the simpler states below.

## Integer price bins

Profiles are keyed by integer bin indices internally. The bin definition is versioned and converts
between an index and its public price boundary. Supported definitions are exchange tick size, fixed
linear step, constant-percentage/log price, a target bin count frozen to supplied low/high bounds,
and a volatility-scaled fixed step frozen to supplied reference inputs. Target-count and
volatility-scaled definitions must be frozen before replay; they are not recalibrated from future
candles.

The POC is the greatest-volume integer bin. Ties resolve deterministically. The value area starts at
the POC and expands contiguously outward until it contains the configured volume fraction; sparse
profiles do not permit the area to jump across missing price indices. Public POC, VAH, and VAL
prices are derived from their integer indices. VWAP uses the allocation model's explicit
price-volume numerator.

## Volume-at-price approximations

The source contains OHLCV candles, not exact trade-at-price observations. Allocation model IDs are
therefore part of profile identity and must be recorded with derived evidence:

- `uniform-touched-v1` spreads a candle's volume equally across every bin touched by its low-high
  range;
- `typical-price-v1` assigns all volume to the `(high + low + close) / 3` bin;
- `triangular-close-v1` weights touched bins linearly toward the close bin;
- `lower-timeframe-v1:<base-model>` aggregates only supplied real constituent candles and rejects
  constituents outside the parent range.

These are approximations, not observations of participant intent or exact traded volume at price.
Lower-timeframe reconstruction never interpolates, forward-fills, or fabricates candles. Exact
trade allocation is not implemented because the restored tick relation contains no observations.

## Explicit profile windows

Profile calculation is independent of window membership. Every `AuctionEngine` requires one
explicit policy:

- `RollingBars` retains a bounded number of included candles;
- `RollingDuration` retains timestamps in `(current - duration, current]`;
- `FixedWindow` includes one half-open UTC range `[start, end)`;
- `UTCDayWindow`, `UTCWeekWindow`, and `UTCMonthWindow` reset at declared UTC session boundaries.

The engine rejects duplicate and out-of-order timestamps and prohibits symbol or timeframe changes
inside one stream. A material interval gap fails closed by default; `GapPolicy.RESET` starts a new
profile and emits a reset event. A canonical segment-ID change always resets state. Session changes
also reset the profile. Node persistence and profile-to-profile migration never cross those hard
boundaries.

## Current deterministic state model

`market_structure_lab.auction.AuctionLocation` classifies the latest close as one of:

- below value
- lower value
- point of control
- upper value
- above value

An empty/zero-value profile also has the explicit `no_value` state. These states are intentionally
simple and derived only from the current close and current bounded profile. They do not by
themselves establish acceptance, rejection, balance, imbalance, or a tradable edge.

## Current structural features

`market_structure_lab.structure.detect_profile_nodes` detects local HVN and LVN zones from adjacent
integer-bin volume. Equal-volume plateaus collapse into one contiguous zone. Detection reports
prominence, width, representative index/price, and zone price bounds. Optional smoothing stays
inside each contiguous run and never bridges absent bins. Endpoints are excluded because they lack
two-sided evidence.

`NodePersistenceTracker` counts consecutive overlap for same-kind zones using the same binning
identity. The auction engine resets persistence at gap, segment, session-window, and explicit reset
boundaries.

`market_structure_lab.structure.compare_value_migration` compares two profiles and classifies value migration as:

- lower
- overlapping lower
- overlapping
- overlapping higher
- higher

This gives transition-analysis code a deterministic vocabulary for value migration before any
probabilistic modelling is introduced.

## Immutable snapshots, events, and replay

Each auction update produces a frozen snapshot containing the current profile, active timestamps,
auction location, nodes, migration, structural events, and all dataset/configuration/window/profile
identities needed to interpret it. Structural event kinds are gap reset, segment reset, session
window reset, POC migration, value breakout, and value re-entry. Event IDs are stable hashes of the
observable stream coordinates and event payload.

Canonical JSON serialization has deterministic key order and timestamp formatting. Hashing the
length-delimited ordered snapshot stream provides replay evidence: identical ordered inputs and
configuration produce the same snapshot bytes and digest. Replay consumes only the current and
prior frozen state; no future candle participates in a snapshot.

## Current transition model

`market_structure_lab.transitions.estimate_cluster_transitions` is the canonical transition
surface. It accepts only UTC-aware `ClusterObservation` values carrying symbol, timeframe, segment,
session, timestamp, and information-cutoff boundaries. Equal adjacent labels are compressed into
dwell runs, and transitions never cross a declared boundary or non-contiguous timestamp.

Returned matrices expose raw observation count, dwell-run count, contiguous-sequence count, and
boundary-break counts by symbol, timeframe, segment, session, and non-contiguous time. Row and
destination estimates expose effective dwell-run support. The estimates are descriptive conditional
frequencies with boundary-preserving block-bootstrap intervals; no raw-adjacent binomial p-value or
Benjamini-Hochberg screening API is available. Discovery manifests and interpretation evidence
packs retain this complete matrix rather than copying rows without their boundary evidence.
