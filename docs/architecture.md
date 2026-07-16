# Architecture

The repository is organised as a single-engineer quantitative research workspace. The emphasis is on reproducible analysis rather than production deployment.

## Repository layout

- data/ stores raw dump files, PostgreSQL persistence, and exports
- docker/ hosts local database and development containers
- docs/ contains the research backlog and project notes
- scripts/ contains operational scripts for inspection and pipeline setup
- `src/market_structure_lab/` contains the installable research package

## Processing pipeline

1. Preserve raw OHLCV data in PostgreSQL or DuckDB
2. Load candles through the canonical dataset layer
3. Update auction state candle by candle
4. Build volume profiles and value areas
5. Detect HVN/LVN regions and auction structure
6. Track deterministic auction states
7. Estimate state-transition probabilities
8. Use statistically significant structure as the basis for later strategy work

## Database layout

The default research database is named `research`. PostgreSQL is intended to be the canonical storage layer for long-lived datasets, while DuckDB can be used for local exploratory analysis when convenient.

## Dataset layer

Research code should import from `market_structure_lab.datasets` and should not contain SQL. The first canonical
interface is:

```python
from market_structure_lab.datasets import load_dataset, load_symbol
```

`load_dataset` returns a Polars frame with ordered OHLCV columns:

```text
timestamp, symbol, timeframe, open, high, low, close, volume
```

Time windows are half-open: `start <= timestamp < end`.

The data layer is the only package allowed to know source tables or columns. Configuration maps the
reviewed `market_data.candles` dump fields into canonical UTC OHLCV. After the recovery migration,
`market_data.candles_canonical` publishes a dump-preferred union: immutable restored rows always win,
and only independently validated append-only supplements can fill absent keys.

Large reads use `market_structure_lab.data.loader.iter_candle_batches`, which executes a server-side
ordered query and yields validated Polars frames with a configurable maximum batch size. Snapshot
exports partition by dataset version, symbol, timeframe, and UTC date; each completed snapshot has
an atomic success marker and a deterministic manifest containing source identities, row counts,
time bounds, and partition hashes.

Material gaps are converted to explicit segment boundaries. The data layer derives these boundaries
from the post-recovery `candles_canonical` series, so a partially recovered original range becomes
only its exact remaining hole or holes. Downstream profile and transition code must not cross those
boundaries.

## Recovery ledger

Missing-candle recovery is separate from the immutable restore. A frozen manifest pins the dump
hash, source row count, mapping version, observed envelopes, exact internal gaps, recovery cutoff,
and per-symbol provenance decision. Binance Spot archive files are accepted only after their
published SHA-256 checksums pass; REST residual responses are content-hashed. Every gap receives a
terminal ledger classification, including source conflicts and authoritative provider absence.

Recovery tables are append-only and the command resumes each gap after its last completed bounded
batch. A PostgreSQL advisory lock serializes recovery publication, and a partial unique index permits
only one validated supplement per canonical `(symbol, interval, open_time)` key. Reapplying a
completed manifest must produce zero new rows and the same logical supplement hash.

## Experiment artifacts

Use `market_structure_lab.experiments.save_experiment_result` for research runs that need durable evidence. Each
run writes:

- `config.json`
- `metrics.json`
- `summary.md`
- `plots/`
- `artifacts/`

The artifact writer is intentionally small. It records outputs; it does not schedule jobs, optimize
strategies, or own research logic.

## Auction engine

Use `market_structure_lab.auction.AuctionEngine` for candle-by-candle deterministic state updates.
The engine accepts exactly one ordered symbol/timeframe stream and requires explicit binning,
allocation, window, dataset-version, and configuration-version inputs. Its default gap policy fails
closed; the alternative resets state at the hard boundary. A segment-ID change always resets state,
and UTC session policies reset at their declared day, Monday-start week, or month boundary.

The profile accumulator stores volume under integer bin indices and supports exact additive removal
of cached candle contributions. Rolling bar-count and duration policies therefore remain bounded
without recomputing the full window. Fixed half-open UTC ranges and UTC day/week/month sessions are
also available. Unbounded cumulative state is not an engine default or implicit fallback.

Each frozen `AuctionSnapshot` records the active timestamps and candle count together with:

- the integer-bin profile, POC, contiguous POC-outward value area, and VWAP;
- close location relative to value;
- HVN/LVN zones, prominence, width, and consecutive-window persistence;
- value migration and stable structural reset, POC-migration, breakout, and re-entry events;
- dataset, config, binning, allocation, profile-definition, window, symbol, timeframe, and segment
  identities.

`market_structure_lab.auction.canonical_snapshot_json` and
`market_structure_lab.auction.snapshot_stream_sha256` serialize and hash ordered snapshots for
deterministic replay evidence. The engine never inspects a future candle.

## Profile representation

`market_structure_lab.profiles` separates bin definitions, volume allocation, profile calculation,
and window membership. Supported bin definitions are exchange tick size, fixed linear step,
constant-percentage/log price, a target-count definition frozen to a supplied range, and a
volatility-scaled definition frozen to supplied reference values.

OHLCV cannot provide exact volume-at-price. Allocation models are therefore explicit and versioned:
uniform touched-bin, typical-price, and triangular-to-close are approximations. Lower-timeframe
reconstruction consumes only supplied real constituent candles; it does not interpolate or invent
observations. Exact trade-price allocation remains unavailable because the restored tick relation
contains no observations.

## Structure layer

Use `market_structure_lab.structure` for deterministic features built from volume profiles. Current
structural primitives include:

- contiguous high-volume and low-volume node zones with optional within-segment smoothing,
  prominence, width, representative prices, and persistence
- point-of-control migration between profiles
- value-area midpoint migration between profiles
- value-area overlap width

Node smoothing never crosses absent integer bins. Persistence resets when the engine crosses a gap,
segment, or window boundary. These functions remain deterministic representation primitives;
statistical significance and transition probabilities belong to later research stages.

## Feature layer

`market_structure_lab.features` is the audited boundary between deterministic auction snapshots and
later discovery work. `FeatureRegistry` freezes each feature's family, category, unit, required
history, missing-value policy, leakage class, and version. `FS-000001` registers 16 auction-informed
and 10 minimally assumptive sequence features. Registry metadata is canonically hashed; formula or
contract changes require a new feature-set identity.

`FeatureBuilder` consumes one ordered snapshot stream with a maximum trailing history of 21 rows.
It rejects symbol/timeframe changes, identity drift, duplicates, ordering errors, and unexplained
gaps. Canonical segment, gap, session, and window resets clear rolling history. Each feature row is
observable at the exclusive close of its source candle and carries all upstream dataset, auction,
profile, window, feature-set, registry, symbol, timeframe, and segment identities.

Training normalisation is an immutable separate artifact. Exact median and IQR statistics are built
only from a declared half-open training partition using bounded on-disk sorted runs and bounded
merge passes. Validation and holdout rows cannot fit scales, and transforms neither refit nor fill
nulls.

## Event layer

`market_structure_lab.events` represents discovery samples as half-open intervals whose information
cutoff is their exclusive end. Every segmenter and detector receives a `FeatureRegistry`, and every
event vector must be a registered discovery-safe row observable at that cutoff. Stable IDs cover
all upstream representation identities, event bounds and kind, trigger version, and feature-vector
content.

Implemented event sources are fixed non-overlapping windows, explicitly exploratory rolling
windows, UTC sessions, causal change points, volatility/volume expansion, Phase 2 value-area and POC
events, and prior-zone node tests/traversals. No event crosses a canonical boundary. Streaming
overlap accounting requires canonical order and retains only active intervals while reporting exact
pair, affected-event, concurrency, and kind-pair evidence.

## Derived dataset publication

`market_structure_lab.data.derived` publishes features and events in bounded deterministic Parquet
parts partitioned by symbol, timeframe, and UTC calendar. A manifest pins dataset, configuration,
profile, window, feature-set, registry, event/normalizer, code, lockfile, schema, missing/leakage,
overlap, row-count, time-bound, and part-checksum evidence.

Publication stages atomically and writes `_SUCCESS` only after full verification. Identical repeats
return the verified result; identity/content disagreements, tampering, stale stages, extra parts,
duplicates, ordering errors, and unsafe schemas fail closed. The derived API remains in
`market_structure_lab.data.derived` rather than the canonical data package root to keep the import
graph acyclic.

## Transition analysis

Use `market_structure_lab.transitions` to count adjacent observed state transitions and estimate conditional
probabilities:

```text
P(next_state | current_state)
```

Rows include support counts, so later research can filter low-observation transitions before
claiming statistical significance.

Use `market_structure_lab.transitions.transition_significance` to compare an observed transition probability
against the unconditional base rate of the next state. The current implementation is a one-sided
binomial tail test for enrichment; it does not correct for multiple comparisons.

Use `market_structure_lab.transitions.screen_transition_enrichment` when testing many observed transitions at once.
It applies Benjamini-Hochberg false-discovery-rate correction and returns only significant
candidates that meet the configured support threshold.
