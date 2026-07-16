# Binance Row-Level Candle Reconciliation Design

## Status

Approved for implementation planning on 2026-07-16.

## Purpose

Replace the current symbol-level Binance compatibility decision with an auditable row-level
reconciliation process. The process must preserve the immutable Callscore dump, identify which
dump candles exactly match official Binance Spot observations, source missing or conflicting
candles from Binance, and publish a corrected derived canonical series without interpolation.

The reconciliation audit covers all 25 existing one-minute symbols. Phase 4 eligibility is decided
only from the frozen reconciliation evidence; BTC, ETH, and other major assets are not excluded
merely because one sampled dump candle differed from Binance.

This work repairs the trustworthy data foundation. It does not attach outcomes, inspect the final
holdout, validate an edge, or construct a strategy.

## Non-negotiable constraints

- `data/dumps/callscore.dump` and restored `market_data.candles` remain immutable.
- Official Binance Spot archives are accepted only after their published SHA-256 checksums verify.
- Binance REST responses are content-hashed and used only for bounded residual ranges.
- Every admitted value retains source, revision, retrieval time, payload hash, and row hash.
- No candle is interpolated, forward-filled, synthesized, or silently selected.
- Processing is bounded by symbol and calendar month and is resumable after interruption.
- Only an explicitly promoted, checksum-verified reconciliation manifest may change canonical
  precedence.
- Existing supplements and recovery evidence remain append-only.
- The final research holdout is not used to select symbols, time ranges, or quality thresholds.

## Architecture

The data model has four layers:

1. **Immutable dump evidence:** restored rows in `market_data.candles`.
2. **Official source evidence:** checksum-pinned Binance observations retrieved through the existing
   archive-first source adapter.
3. **Reconciliation evidence:** deterministic per-key classifications and aggregate manifests.
4. **Derived canonical view:** a promoted reconciliation result that selects verified Binance
   corrections and fills, or an exact verified dump row.

The current recovery system remains valid historical evidence. Reconciliation extends it rather
than rewriting its tables or pretending that the earlier symbol-level decision was a row-level
audit.

## Audit scope and time envelope

One reconciliation run freezes:

- a UTC cutoff;
- the immutable dump identity and mapping version;
- all 25 reviewed symbols;
- the `1m` timeframe;
- Binance Spot as the candidate venue and market type;
- the code commit and lockfile hash;
- the source adapter and reconciliation algorithm versions.

Expected minutes are evaluated only inside each symbol's verified Binance trading envelope.
Minutes before listing or after confirmed delisting are outside the audit envelope rather than
missing candles. The trading-envelope evidence and its derivation are pinned in the manifest.

The first implementation may infer the envelope from verified official Binance observations plus
authoritative empty source ranges. It must fail closed when it cannot distinguish a real
non-trading boundary from source unavailability.

## Per-key reconciliation

Within the frozen trading envelope, the ordered union of dump and Binance keys is classified as:

- `exact_match`: both rows exist and every normalized candle field matches exactly;
- `binance_correction`: both rows exist but one or more normalized fields differ;
- `binance_fill`: a Binance row exists where the dump has no row;
- `source_unavailable`: Binance evidence required to verify or fill the key is unavailable.

Fields compared are:

- symbol;
- timeframe;
- open timestamp;
- open;
- high;
- low;
- close;
- base volume;
- quote volume;
- trade count.

Timestamp normalization must handle the Binance archive's millisecond and microsecond formats.
Numeric comparison uses the existing exact decimal representation. It must not use floating-point
tolerances to convert a mismatch into a match.

Each ledger record includes:

- run ID, symbol, timeframe, and open timestamp;
- classification;
- dump row hash when present;
- Binance row hash when present;
- a deterministic differing-field mask;
- source name and revision;
- payload and published archive checksums where applicable;
- retrieval timestamp;
- work-unit and manifest identities.

## Bounded data flow

The run is split into deterministic symbol/month work units.

1. Freeze the run manifest and work-unit list before retrieval.
2. Fetch official Binance monthly or daily archives into the existing content-addressed cache.
3. Verify the published archive checksum before reading any row.
4. Use the REST API only for bounded residual ranges not covered by a verified archive.
5. Stream dump rows and Binance rows in canonical key order.
6. Compare and emit reconciliation ledger records without materializing a complete symbol.
7. Write ledger data to deterministic symbol/year/month Parquet partitions through sibling staging
   paths.
8. Verify row counts, key bounds, ordering, uniqueness, and checksums before writing `_SUCCESS`.
9. Publish only corrections and fills to the database; exact matches remain backed by immutable
   dump rows and the frozen ledger.
10. Resume at the first absent or unverified work unit. An identical completed rerun verifies the
    existing output without rewriting it.

The configured input, comparison, and Parquet buffers are recorded in each manifest. Exceeding a
bound fails the work unit rather than silently increasing memory use.

## PostgreSQL publication

Add append-only reconciliation tables rather than changing `market_data.candles`:

- reconciliation runs and their frozen identities;
- promoted reconciliation manifests;
- promoted contiguous coverage intervals verified against Binance;
- verified Binance replacement rows for `binance_correction` and `binance_fill`;
- compact work-unit publication/checkpoint records.

The database does not need one new row for every `exact_match`. The immutable dump row, ledger
partition, and promoted manifest together prove its eligibility.

Replacement rows are unique within a promoted run by `(symbol, interval, open_time)`. Mutation
triggers reject updates and deletes. A PostgreSQL advisory lock serializes promotion with recovery,
freshness, and snapshot publication.

Promotion verifies:

- all referenced ledger partitions and source artifacts;
- the frozen dump and mapping identity;
- the complete replacement-row logical hash;
- absence of duplicate keys;
- exact agreement between ledger classifications and replacement rows;
- terminal status for every work unit.

A run containing `source_unavailable` keys may be retained as audited evidence, but those keys do
not enter the corrected canonical series. Promotion splits verified coverage into explicit
half-open contiguous intervals around unavailable ranges, so PostgreSQL does not need one
`exact_match` record per minute.

## Canonical precedence

The corrected research view uses this precedence for an eligible key:

1. a verified Binance correction or fill from the explicitly promoted manifest;
2. an immutable dump row inside a promoted verified coverage interval, unless a correction replaces
   that key;
3. no row.

It must never use:

- the latest retrieved row merely because it is newest;
- an unpromoted reconciliation run;
- a conflicting dump row;
- an unverifiable dump row;
- interpolation or a prior/next candle substitute.

The view exposes origin and reconciliation lineage so downstream snapshots can identify whether a
row is a verified dump match, Binance correction, or Binance fill.

Promoted coverage intervals are derived from the complete per-key ledger and are checked against its
counts and hashes during promotion. They are an index for canonical selection, not a substitute for
the detailed Parquet audit evidence.

The existing dump-preferred `market_data.candles_canonical` remains available until reconciliation
promotion and migration verification are complete. The corrected view receives a distinct name
during rollout; switching the configured canonical mapping is a deliberate final step.

## Manifests and audit evidence

Each work-unit manifest records:

- requested and observed UTC bounds;
- dump, Binance, and union row counts;
- counts for every classification;
- differing-field counts;
- source artifact identities and checksums;
- ledger partition path and checksum;
- replacement-row count and logical hash;
- maximum observed buffers;
- completion status and error evidence.

The global manifest records:

- all frozen run identities;
- trading envelopes;
- ordered work-unit hashes;
- per-symbol classification and coverage totals;
- unresolved ranges;
- database replacement logical hash;
- corrected canonical row count and logical hash;
- symbols and time ranges eligible for snapshot publication.

Every successful, failed, partial, and unavailable result remains visible. Re-running identical
inputs must produce the same logical identities.

## Eligibility for the Phase 4 market pilot

The audit covers all 25 symbols. It does not preselect a research subset based on future outcomes.

A symbol/time range is eligible when:

- every expected minute in the proposed range has a corrected canonical row;
- every row is either an exact verified dump match, Binance correction, or Binance fill;
- there are no unresolved source keys;
- the promoted manifest, database logical hash, and canonical logical hash verify;
- the range is long enough to support the already frozen discovery, development, and final-holdout
  split policy.

The initial major-asset candidate set includes BTC, ETH, BNB, SOL, XRP, ADA, DOGE, LINK, and AVAX,
plus other reconciled assets useful for asset coverage and holdouts. The final eligible set is a
data-quality result, not an outcome-driven selection.

After eligibility is frozen, a separate deliberate operation publishes the immutable `DS-*`
snapshot. Phase 3 feature/event publication and the real outcome-blind Phase 4 discovery run follow
from that snapshot.

## Failure handling

- Invalid archive checksum: quarantine the payload and fail the work unit.
- Duplicate or unordered Binance keys: fail the work unit.
- Invalid OHLCV or timestamp grid: fail the work unit.
- Source unavailable: record the exact range and retain an incomplete audited run.
- Dump identity drift: fail the run before publication.
- Existing identity with changed content: fail closed.
- Interrupted retrieval or comparison: preserve verified work units and resume the remainder.
- Promotion disagreement or checksum failure: publish nothing to the corrected canonical view.
- Concurrent recovery, reconciliation promotion, or snapshot publication: fail to acquire the
  shared advisory lock and exit without mutation.

## Testing and verification

Unit tests cover:

- exact decimal equality and every differing-field mask;
- milliseconds and microseconds;
- exact match, correction, fill, and unavailable classifications;
- listing-envelope boundaries;
- empty ranges, gaps, duplicates, invalid OHLCV, and unordered input;
- bounded buffers and deterministic partitioning;
- manifest identity, hashing, idempotence, tamper rejection, and stale stages;
- promotion guards and canonical precedence;
- no fallback to conflicting or unverifiable dump rows.

PostgreSQL integration tests use disposable databases and prove:

- append-only replacement storage;
- unique promoted keys;
- advisory-lock exclusion;
- exact-match dump selection;
- Binance correction/fill precedence;
- omission of unresolved keys;
- identical logical hashes across replay.

Operational verification includes:

- a small real Binance archive pilot with published checksum evidence;
- a bounded BTC and ETH reconciliation slice containing known conflicting samples;
- comparison of the corrected output at those samples;
- full audit dry-run accounting before database promotion;
- Ruff, formatting, Mypy, targeted tests, full Pytest, package build, Compose validation, and
  `git diff --check`.

No destructive database reset, volume deletion, or source-dump modification is part of this work.

## Rejected alternatives

### Rebuild everything from Binance and discard the dump

This would simplify precedence but discard useful immutable evidence, duplicate exact-match data,
and weaken the audit trail connecting prior work to the corrected dataset.

### Retain symbol-level compatibility

This quarantines millions of usable BTC, ETH, and other major-asset rows because of isolated
mismatches. It cannot identify which rows are canonical.

### Allow Binance supplements only for missing dump keys

This preserves known conflicting dump observations and therefore cannot produce a trustworthy
single-venue canonical series.

### Overwrite restored dump rows

This violates raw-data immutability and destroys the evidence needed to reproduce and explain each
correction.

## Completion boundary

This design is complete when all 25 symbols have a frozen reconciliation audit, a promoted
corrected canonical view exists for successfully verified keys, completeness and logical hashes
verify, and an eligible major-asset `DS-*` snapshot can be deliberately frozen.

That completion advances the project into real Phase 4 market experiments. It does not advance into
Phase 5 outcome validation.
