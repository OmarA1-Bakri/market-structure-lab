# Implementation Status

Evidence date: 2026-07-16

## Scope

This execution implements the approved data-truth foundation, early missing-candle recovery,
canonical snapshot boundary, deterministic auction representation, and Phase 3 feature/event
dataset boundary. It does not begin behaviour discovery, clustering, AI hypothesis generation,
statistical edge validation, strategy construction, exchange integration, or live execution.

## Foundation delivered

- The installable package now lives under `src/market_structure_lab/`; imports, scripts, tests, and
  Hatch packaging use `market_structure_lab.*`.
- Frozen typed database and source-mapping configuration validates identifiers, ports, timestamp
  units, and constructs credential-safe SQLAlchemy URLs.
- `msl-db-inspect` performs real schema discovery and deterministic quick/full data-quality
  inspection. JSON and human output redact database credentials.
- Placeholder operational scripts were removed. Remaining scripts are thin package entry points.
- The reviewed mapping is `market_data.candles`, epoch milliseconds, `interval` as timeframe,
  source OHLCV plus optional quote volume/trades, mapping version `market-data-candles-v1`.
- Canonical reads use ordered server-side batches. Snapshot export is atomic, UTC-date partitioned,
  checksum-manifested, and derives exact unresolved-gap segment boundaries from the post-recovery
  canonical series.

## Immutable restore evidence

- Dump path: `data/dumps/callscore.dump`
- Bytes: `889,379,858`
- SHA-256: `1B6BCB39AF41048B53729E9B094F0229163EB6FF6AF9563ADB666C96F5FD4DA4`
- Fresh isolated restore source rows: `35,748,117`
- Fresh isolated restore symbols: `25`
- Restored source relations: `market_data.candles`, `market_data.candles_id_seq`,
  `market_data.ticks`, and `market_data.ticks_id_seq`
- Callscore-derived candle columns and Callscore creator/application relations: absent
- The successful isolated restore volume was preserved and restarted for integration verification;
  `data/postgres` was neither deleted nor reinitialized.

The restore initially exposed two fail-fast script defects: PostgreSQL had already moved owned
sequences with their tables, and a shell-style comment appeared inside a SQL heredoc. Both defects
have regression coverage, and a subsequent fresh isolated restore completed successfully.

## Source inspection baseline

Full exact inspection of the immutable source reported:

- `35,748,117` candles, `25` symbols, UTC range 2017-11-25 through 2026-07-14;
- zero duplicate candle keys, invalid OHLC rows, and required-field nulls;
- `19,192` internal gap runs containing `23,372,460` missing minute slots;
- largest internal gap: `2,554,080` minutes;
- optional quote-volume/trade fields null on `17,040,419` rows;
- `market_data.ticks`: zero rows.

The frozen baseline recovery manifest hash is
`befbf514ead328e14320cd6097ce28663c652bc9136115d344fa6714023d826d`.

## Binance Spot compatibility gate

The validated manifest hash is
`482f3a09a4e24a62b0ad92f5bb90028eead67fe961eb1d3320dd38d13fd105d2`.
Every symbol was checked independently using deterministic distributed and major-gap-boundary
overlaps. Sixteen symbols passed and nine failed closed:

- compatible: ADA, ALGO, APT, BNB, DOGE, DOT, FTM, IMX, INJ, LINK, NEAR, TAO, THETA, XLM, XRP,
  ZEC;
- source conflict: AR, AVAX, BTC, ETH, FET, PENDLE, RENDER, SOL, SUI.

The conflict group covers `9,194` gaps and `6,863,688` missing minutes. No supplement is admitted
for those symbols. Their evidence records the exact sampled timestamp and differing fields rather
than inferring a venue.

## Verification evidence

The pre-existing root `.venv` contains a stale Windows symlink. It was preserved rather than
rewritten; verification used the ignored `.venv-ralph` environment via
`UV_PROJECT_ENVIRONMENT=.venv-ralph`. This implementation introduced no new dependency; the lock
now matches the existing `pyproject.toml` requirements.

The foundation and recovery verification produced:

- `uv sync --locked` and `uv lock --check`: passed;
- unit/regression suite: `108 passed` in the final recovery evidence pass;
- Ruff: passed;
- Mypy across `src`: passed with no issues in 33 source files;
- wheel and source distribution: built successfully;
- isolated installed-wheel import: passed with runtime dependencies installed;
- `docker compose config --quiet`: passed;
- `bash -n docker/postgres/init/restore_dump.sh`: passed;
- archive TOC/allowlist inspection: passed;
- fresh isolated PostgreSQL restore and exact relation/source-row assertions: passed;
- `git diff --check`: passed.

## Completed recovery evidence

- Run ID: `381be98e-51fb-4a07-9ad6-a0fdd44305a7`
- Terminal gaps: `19,192 / 19,192`
- Added unique supplements: `16,480,681`
- Immutable source rows before/after: `35,748,117 / 35,748,117`
- Remaining missing minutes: `6,891,779`
- Failed batches: `0`
- Supplements on source-conflict symbols: `0`
- Logical supplement hash:
  `e79c147039249362feba0ef153ea7417bcabab7a3e0c0c670e414f91f2adf6e1`
- Reapplying the identical validated manifest: `0` inserted rows, all `19,192` gaps complete,
  identical logical hash.
- Durable batch ledger: `28,765` completed bounded checkpoints. Interrupted gaps resume after the
  greatest completed batch boundary and retain prior authoritative-empty evidence.
- Publication is globally serialized with a PostgreSQL advisory lock; the validated-key unique
  index is present and the real supplement table has zero duplicate canonical keys.

The resolution ledger contains 9,869 fully recovered gaps, 117 partially recovered gaps, 12
authoritative provider-absent gaps, and 9,194 source-conflict gaps. Coverage conservation is exact:
`23,372,460 - 16,480,681 = 6,891,779` missing minutes after recovery.

## Canonical snapshot proof

A real bounded export used `batch_size=257` for XRPUSDT on 2018-05-05. The canonical view returned
exactly 1,440 ordered minutes: one immutable dump row and 1,439 validated supplements. Atomic
publication produced one UTC-date Parquet partition with SHA-256
`225096cddf0dd2081c8a51ba9f9ea575aea814d08d497288f87975536f813b32` and snapshot hash
`5c98cb44467f02d20529c40489f2758b20c83347a56ab951e4879879465e66f6`.
Repeating the export returned the existing verified snapshot with the same row count and hash.

The exact post-recovery boundary query was also run against the complete XRPUSDT canonical series.
It returned `27` disjoint remaining gaps totaling exactly `5,902` minutes, with a largest gap of
`600` minutes and UTC-aware bounds. The original ledger reports `26` non-full ranges because one
partially recovered original range split into multiple exact remaining holes.

## Phase 2: auction representation

Phase 2 implements the deterministic auction-representation boundary described in PRD sections 16
and 17. It does not begin feature/event dataset production, behaviour discovery, statistical edge
validation, strategy construction, exchange integration, or live execution.

Delivered representation:

- versioned integer bin definitions for exchange ticks, fixed steps, constant-percentage/log price,
  frozen target counts, and frozen volatility-scaled steps;
- explicit, versioned OHLCV allocation approximations for uniform touched-bin, typical price, and
  triangular-to-close volume, plus reconstruction from supplied real lower-timeframe candles only;
- deterministic integer-bin POC, contiguous POC-outward value area, VWAP, immutable profiles, and
  additive/removable rolling accumulation;
- required bounded window policies for rolling bar count, rolling duration, fixed half-open UTC
  ranges, and UTC day/week/month sessions;
- ordered symbol/timeframe stream checks, hard segment resets, explicit reject-or-reset gap policy,
  and no transition or persistence across invalid boundaries;
- immutable versioned auction snapshots containing active timestamps, profile state, location,
  node zones and persistence, value migration, and stable structural events;
- canonical snapshot serialization and deterministic ordered replay hashes;
- golden profile/replay fixtures and rolling incremental-versus-full-recomputation coverage.

Research limitations remain explicit: all candle-based allocation models are approximations, exact
trade-price allocation is unavailable, and no candle is interpolated, forward-filled, or fabricated.
Session boundaries are UTC conventions, not exchange closure claims. The simple location and event
vocabulary is representation evidence, not a behaviour, hypothesis, validated edge, or strategy.

### Phase 2 verification evidence

The existing root `.venv` remained untouched after Windows could not remove its stale `lib64`
symlink. Phase 2 verification used the isolated temporary uv environment
`C:\Users\albak\AppData\Local\Temp\msl-phase2-venv`; the built wheel was installed into a second
fresh temporary environment.

- Unit/regression suite: `343 passed in 7.85s` in the final pass.
- Ruff: all repository checks passed.
- Mypy: no issues in `40` source files.
- `uv lock --check`, `uv sync --locked`, wheel build, and source-distribution build: passed.
- Isolated installed-wheel auction update and canonical snapshot hash: passed; smoke hash
  `0f48041af27caea054fe569ff2c4ab4fcf2aa3f9bc850708b9737b664b811922`.
- Golden rolling replay: whole-stream SHA-256
  `6ce85846132da118bc5cd0d73c1e37eea13a0cd3b55eaa170f336944b38a79b1`, with each intermediate
  snapshot hash independently pinned in the fixture.
- Rolling incremental/full equivalence: unit coverage across uniform and typical-price allocation,
  windows of 1, 2, and 60 bars over 512 snapshots each; the benchmark independently compared 512
  snapshots at absolute tolerance `1e-12`.
- Benchmark: [`docs/benchmarks/phase2-auction-baseline.json`](benchmarks/phase2-auction-baseline.json)
  records 100,000 deterministic candles, seed `20260716`, input SHA-256
  `3a8127988b56f0b1a2e873143aac22c58284e651b4b1fe66bd24b021dfeda6d4`, canonical snapshot-stream
  SHA-256 `d879aa0010852ed9c6ca24ee1d5d217458459dffcc7091833ec5219d302a0e3f`, 523.43 candles/second,
  1,440 maximum active candles, and 1.03 MiB peak traced engine memory. No machine-specific timing
  threshold is asserted.
- Independent architect review: approved after `237` targeted tests and explicit reruns of the
  decimal-boundary, empty-profile, fixed-exclusion, session-reset, and golden-hash counterexamples.
- `docker compose config --quiet`, restore-script shell syntax, `git diff --check`, and immutable
  dump SHA-256 verification: passed.

The Phase 2 exit gate is satisfied.

## Phase 3: feature and event datasets

Phase 3 implements the outcome-blind evidence boundary described by the approved Phase 3 PRD and
test specification. It consumes Phase 2 snapshots and does not attach forward outcomes or begin
behaviour discovery.

Delivered evidence contracts:

- `FS-000001`, a canonical hashed registry containing 16 auction-informed and 10 minimally
  assumptive sequence features with explicit units, history, missing-value, version, category, and
  leakage metadata;
- a causal builder bounded to 21 trailing snapshots, exact exclusive-close information cutoffs,
  hard resets at segment/gap/session/window boundaries, and fail-closed upstream identity checks;
- exact training-only median/IQR normalisation using bounded on-disk sorted runs and bounded merge
  buffers, with immutable artifact hashes and no validation/holdout fitting;
- half-open discovery events with mandatory registry validation, provenance-rich stable IDs,
  feature-vector hashes, causal prior-history triggers, and source-open versus observable-trigger
  timestamp separation;
- fixed, exploratory rolling, UTC session, causal change-point, expansion, value/POC, and prior-zone
  node event construction without boundary crossing;
- exact streaming overlap accounting whose memory is proportional to active concurrency rather than
  the total event collection;
- bounded deterministic Parquet publication with pinned upstream/code/lock identities, registered
  schemas, checksums, missing/leakage/overlap evidence, atomic `_SUCCESS`, tamper detection, and
  content-idempotent repeats.

The public formulas, null rules, event semantics, and operations contract are documented in
[`FEATURE_EVENT_DATASETS.md`](FEATURE_EVENT_DATASETS.md). Derived publication APIs live at
`market_structure_lab.data.derived`; keeping them out of the canonical data package root prevents
fresh-install circular imports.

### Phase 3 verification evidence

The stale root `.venv` remained untouched. Phase 3 verification used the ignored
`.venv-codex` environment through `UV_PROJECT_ENVIRONMENT=.venv-codex`; builds and installed-wheel
checks used isolated directories below the operating-system temporary directory.

- Post-deslop unit/regression suite: `473 passed in 15.74s`.
- Ruff: all repository checks passed.
- Ruff formatter: all `20` Phase 3-touched Python files already formatted after the scoped cleanup.
- Mypy: no issues in `53` source files.
- `uv lock --check` resolved the pinned `120` packages without a change; `uv sync --locked` passed.
- Post-deslop wheel and source distribution built successfully; a fresh installed-wheel smoke
  printed `FS-000001 26 change_point True True DerivedPublicationIdentity`, including both
  documented event callables.
- Feature and event publication tests include fresh-interpreter import, deterministic batching,
  empty publication, idempotent replay, stale/tampered output, schema leakage, and overlap evidence.
- Docker daemon `29.4.2` was reachable and `docker compose config --quiet` passed.
- `bash -n docker/postgres/init/restore_dump.sh` and `pg_restore --list` passed.
- The dump SHA-256 was rechecked as
  `1B6BCB39AF41048B53729E9B094F0229163EB6FF6AF9563ADB666C96F5FD4DA4`.
- Read-only queries against preserved isolated PostgreSQL containers reconfirmed `35,748,117`
  immutable source candles, `16,480,681` validated supplements, `19,192` terminal gap resolutions,
  `28,765` completed recovery batches, zero Callscore application tables outside the approved
  schemas, and zero forbidden Callscore-derived candle columns.
- The exact 100,000-candle Phase 2 benchmark regression wrote only to a temporary file. Its schema
  and complete correctness block match the pinned baseline: input SHA-256
  `3a8127988b56f0b1a2e873143aac22c58284e651b4b1fe66bd24b021dfeda6d4`, snapshot-stream SHA-256
  `d879aa0010852ed9c6ca24ee1d5d217458459dffcc7091833ec5219d302a0e3f`, `512` incremental/full
  comparisons passed at absolute tolerance `1e-12`, repetitions were identical, and maximum active
  candles remained `1,440`. Timing is not an acceptance threshold.
- Independent architect review: approved after `135` focused tests and adversarial checks of
  punctuation-hidden future metadata, identity drift, cutoffs, boundary resets, bounded
  normalisation, event identity, structural observability, overlap, publication, package imports,
  and Phase 4 exclusion.
- Mandatory anti-slop pass: the architect found no blocking overengineering; scoped Ruff formatting
  changed only `12` Phase 3-owned files, removed irregular indentation and formatting drift, and
  retained `130` focused passing tests before the complete post-cleanup gate above.
- Final `git diff --check`: passed. Only preserved user/runtime `.codacy`, `.coverage`, and `.vscode`
  artifacts remain untracked and were not staged.

The Phase 3 exit gate is satisfied with zero known implementation errors. Phase 4 behaviour
discovery remains unstarted and requires a separate explicit approval.

## Daily candle freshness implementation

The approved freshness prerequisite is implemented without changing the immutable dump or existing
research snapshots. `msl-sync-candles` supports frozen planning, dry-run/apply, checksum-bearing
reports and health, plus an explicit `snapshot` handoff. The handoff checks the report,
compatibility artifact, dump identity, current logical supplement hash, and publication policy; it
then streams bounded canonical batches through the existing atomic partitioned exporter.

The default snapshot policy requires every symbol to be `up_to_date` or `recovered`. An explicit
`allow_provenance_blocked` policy can freeze uneven research coverage only when every exception is
`provenance_pending`, `source_conflict`, or `source_unavailable`; it does not admit partial,
provider-absent, failed, or unresolved recovery. The nine existing source conflicts therefore keep
full-universe scheduler health non-zero and are recorded in any explicitly permitted snapshot.

Implementation verification performed for this slice:

- focused snapshot/CLI/export tests: `22 passed`;
- disposable PostgreSQL 17 integration: `1 passed`, proving migration reapplication, dump-preferred
  reads, append-only enforcement, same-cutoff idempotency, later-cutoff-only recovery,
  blocked-source no-fetch, advisory-lock exclusion, and report conservation;
- the disposable container used no repository volume and was removed after the test;
- no live Binance request, production database sync, or automation schedule was executed.

Exact operator commands, paths, status meanings, resumption procedure, compatibility hash, and the
deliberate snapshot policy are documented in
[`DAILY_CANDLE_FRESHNESS.md`](DAILY_CANDLE_FRESHNESS.md). Repository-wide gates remain to be recorded
by the integrating verification pass; this section does not claim them in advance.
