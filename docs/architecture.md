# Architecture

The repository is organised as a single-engineer quantitative research workspace. The emphasis is on reproducible analysis rather than production deployment.

## Repository layout

- data/ stores raw dump files, PostgreSQL persistence, and exports
- docker/ hosts local database and development containers
- docs/ contains the research backlog and project notes
- scripts/ contains operational scripts for inspection and pipeline setup
- `src/market_structure_lab/` contains the installable research package

## Processing pipeline

1. Preserve immutable raw OHLCV and publish canonical quality evidence
2. Reconstruct deterministic auction state candle by candle
3. Publish versioned causal features and structural events
4. Discover recurring behaviours without outcomes
5. Let AI interpret frozen evidence and propose falsifiable theories
6. Validate frozen candidates on untouched data under statistical controls
7. Promote only robust cost-adjusted edges into strategies and portfolios

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
and only independently validated append-only supplements can fill absent keys. The canonical research
history starts at `2018-01-01T00:00:00Z`; pre-2018 rows remain immutable raw evidence but are excluded
from canonical views, reconciliation plans, snapshots, and experiments.

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
Checksum-valid archive rows that do not lie on the one-minute UTC grid are quarantined rather than
rounded. Their count and first/last timestamps remain in the work-unit provenance; the affected
canonical minute keys remain unavailable and form sequence boundaries. Listing boundaries,
maintenance halts, delistings, and venue outages are non-trading boundaries, never candles to
synthesize.

Recovery tables are append-only and the command resumes each gap after its last completed bounded
batch. A PostgreSQL advisory lock serializes recovery publication, and a partial unique index permits
only one validated supplement per canonical `(symbol, interval, open_time)` key. Reapplying a
completed manifest must produce zero new rows and the same logical supplement hash.

## Daily freshness and snapshot handoff

After a deliberate crypto-only restore, the explicit idempotent `msl-sync-candles bootstrap`
operation verifies dump and compatibility identities before applying the existing append-only
recovery migration. It performs no source fetch and is never invoked implicitly by the scheduler.

`msl-sync-candles` plans against `market_data.candles_canonical` through an explicit half-open UTC
cutoff, so internal holes and the missing tail use the same append-only recovery engine. The live
canonical view becomes current as validated supplements commit; no dump row is updated and
provenance-blocked symbols produce no fetch requests.

The PostgreSQL planner uses one aggregate summary and a lag-only ordered key scan per symbol. Both
operate on narrow `(symbol, interval, open_time)` keys and merge the dump with validated supplements
while excluding dump collisions. Transaction-local planner controls keep this path on index-only
merge scans and prevent sequential/hash/sort plans from creating unbounded temporary spills.

The unattended Windows runner requires an existing `data/postgres/PG_VERSION`, holds a host lock
for the workflow, and relies on the recovery advisory lock for publication. It atomically preserves
`pending.json` across retryable failures and advances `latest.json` only for a terminal report. It
never bootstraps, snapshots, deletes volumes, or reinitializes PostgreSQL.

Immutable research snapshots are a separate deliberate operation. A checksum-verified freshness
report is accepted only when healthy or when the operator explicitly selects the narrowly defined
provenance-blocked policy. Publication holds the recovery advisory lock, verifies that the report's
complete logical supplement hash still matches PostgreSQL, streams each sorted symbol through
`iter_candle_batches`, and reuses `export_partitioned_snapshot`. Its identity pins the dump,
supplement state, freshness plan/report, compatibility review, cutoff, policy, mapping, config, and
code commit. This prevents a daily scheduler from silently replacing a frozen research dataset.

## Experiment artifacts

`market_structure_lab.experiments` is the sole terminal trial-accounting contract. Every new
discovery, hypothesis, validation, or strategy attempt publishes one canonical
`trial-receipt-v2` bundle under `data/exports/trials/`. The receipt pins the dataset snapshot,
feature publication, registry, normalizer, frozen split, detector/candidate, code and lock,
canonical parameters, seed, universe/ranges, parents, metric schema, warnings, conclusion, UTC
lifecycle timestamps, terminal status, and every artifact hash. Validation and strategy additionally
require frozen outcome and cost policies. These identities are required and receipt/artifact bytes
are verified here; proof of the complete snapshot-to-feature-to-normalizer derivation chain remains
a later, post-Phase-0 Phase 4 hardening gate.

Publication writes a sibling staging directory, verifies canonical bytes, and atomically renames it.
Published bytes are never overwritten: identical replay is idempotent, while content conflicts,
stale staging, missing files, extra files, and checksum drift fail closed. The execution boundary
records `failed` or `abandoned` before re-raising the original algorithm exception. Normal output
must end `completed`, `rejected`, or `inconclusive`. `experiment-manifest-v1` remains a readable,
explicitly legacy format and receives no invented provenance.

Canonical Phase 4 discovery includes its checksum-pinned detector, stability, motif, transition,
representative, and discovery-manifest files as artifacts of the same terminal receipt. Later AI
interpretations publish into a separately checksum-verified sibling namespace
`data/exports/trials-interpretations/`; they never add files below an immutable receipt directory or
poison ledger enumeration. Synthetic golden replay remains legacy fixture evidence and is never
counted as a real trial.

Daily PostgreSQL freshness does not automatically create a research dataset. Real Phase 4 market
experiments begin only after a deliberate immutable candle snapshot and Phase 3 feature/event
publication. Phase 4 transitions are boundary-aware, event-level and dwell-compressed; they are
Markov-like conditional summaries, not proof of a stationary first-order Markov process.

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
parts partitioned by symbol, UTC year, and UTC month. Timeframe remains an ordered, validated schema
field. A manifest pins dataset, configuration, profile, window, feature-set, registry,
event/normalizer, code, lockfile, schema, missing/leakage, overlap, row-count, time-bound, and
part-checksum evidence.

Publication stages atomically and writes `_SUCCESS` only after full verification. Identical repeats
return the verified result; identity/content disagreements, tampering, stale stages, extra parts,
duplicates, ordering errors, and unsafe schemas fail closed. The derived API remains in
`market_structure_lab.data.derived` rather than the canonical data package root to keep the import
graph acyclic.

## Outcome-blind discovery layer

`market_structure_lab.discovery` is the boundary between frozen Phase 3 evidence and later outcome
evaluation. `FrozenDiscoverySplit` canonically fixes discovery, development, final-holdout, and
optional asset-holdout metadata. Fit inputs can contain discovery rows only; development rows are
admitted only for stability, and holdout iterables are rejected before access.

The baseline remains deliberately inspectable: a capped registered numeric matrix, deterministic
PCA, seeded canonical K-means, seeded/subsample/adjacent-period/asset/parameter stability evidence,
bounded motif search, and event-level boundary-aware transitions. Stable definitions become
content-addressed `FrozenBehaviour` records; unstable definitions remain explicit rejected runs
with no behaviours.

`run_discovery` stages canonical artifacts and atomically publishes checksum-pinned `DR-*`
directories. Replay verifies all hashes and returns existing identical content without rewriting it.
AI interpretation is a separate publication stage. It receives frozen summary evidence only,
preserves detector fields, uses neutral/inference-labelled language, and records complete
provider/model/prompt/response provenance. Neither discovery nor interpretation can access Phase 5
outcomes or the final holdout.

The exact contracts, caps, golden hashes, replay command, and approximation limits are documented
in [`DISCOVERY_MVP.md`](DISCOVERY_MVP.md).

## Transition analysis

Use `market_structure_lab.transitions.estimate_cluster_transitions` to estimate conditional
frequencies from boundary-aware `ClusterObservation` records:

```text
P(next_state | current_state)
```

The estimator compresses repeated dwell rows and partitions observations at symbol, timeframe,
segment, session, and non-contiguous-time boundaries before counting. Each matrix records raw-row,
dwell-run, contiguous-sequence, and boundary-break evidence; its rows and destinations expose
effective dwell-run support. Boundary-preserving block-bootstrap intervals quantify sampling
variation without claiming independent minute observations. The legacy raw-adjacent matrix,
binomial p-value, and Benjamini-Hochberg screening APIs are intentionally unavailable.
